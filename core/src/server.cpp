#include "drop.grpc.pb.h"
#include "drop/catalog.hpp"
#include "drop/config.hpp"
#include "drop/database.hpp"
#include "drop/domain.hpp"
#include "drop/json.hpp"
#include "drop/process.hpp"
#include "drop/telemetry.hpp"

#include <grpcpp/grpcpp.h>
#include <nlohmann/json.hpp>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <csignal>
#include <fstream>
#include <memory>
#include <set>
#include <thread>

namespace dv = drop::v1;
using json = nlohmann::json;

namespace dropcore {
namespace {
std::string peer_name(grpc::ServerContext *context) {
  auto auth = context->auth_context();
  if (!auth || !auth->IsPeerAuthenticated())
    return "";
  auto values = auth->FindPropertyValues("x509_common_name");
  if (values.size() != 1)
    return "";
  return std::string(values.front().data(), values.front().size());
}
void require_agent(grpc::ServerContext *context, const std::string &id) {
  if (!safe_identifier(id) || peer_name(context) != "drop-agent-" + id)
    throw grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                       "Agent certificate does not match identity");
}
void require_api(grpc::ServerContext *context) {
  if (peer_name(context) != "drop-api")
    throw grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                       "API certificate required");
}
void ack(dv::Ack *response) { response->set_ok(true); }
template <class F> grpc::Status guarded(F &&action) {
  try {
    action();
    return grpc::Status::OK;
  } catch (const grpc::Status &status) {
    return status;
  } catch (const std::invalid_argument &e) {
    return {grpc::StatusCode::INVALID_ARGUMENT, e.what()};
  } catch (const std::exception &) {
    log("drop-server", "RPC database or internal operation failed", "",
        "INTERNAL");
    return {grpc::StatusCode::UNAVAILABLE,
            "Control plane temporarily unavailable"};
  }
}
void event(Database &db, const std::string &task, const std::string &attempt,
           const std::string &type, const json &payload = json::object()) {
  db.exec("INSERT INTO task_events(task_id,attempt_id,type,source,payload) "
          "VALUES($1::bigint,$2::bigint,$3,'drop-server',$4::jsonb)",
          {task, attempt, type, payload.dump()});
}
// Terminal authorization retains only the fixed public diagnostic metadata;
// rebuilding the object discards every credential/unknown extension field.
constexpr char terminal_log_grants_sql[] =
    R"SQL(COALESCE((SELECT jsonb_agg(jsonb_build_object(
'name','runner.log','kind','LOG','object_key',v->>'object_key',
'format','log_jsonl','content_type','text/plain','max_file_bytes',16384))
FROM jsonb_array_elements(upload_targets) v
WHERE v->>'kind'='LOG' AND v->>'name'='runner.log'
AND v->>'format'='log_jsonl' AND v->>'content_type'='text/plain'
AND v->'max_file_bytes'='16384'::jsonb
AND v->>'object_key'='raw/tasks/'||task_id||'/attempts/'||attempt||'/runner.log'),
'[]'::jsonb))SQL";
void fail(Database &db, const std::string &task, const std::string &attempt,
          const std::string &code, const std::string &message,
          int exit_code = -1) {
  db.exec(
      "UPDATE tasks SET "
      "collection_status='FAILED',analysis_status='FAILED',error_code=$2,error_"
      "message=$3,updated_at=now(),version=version+1 WHERE id=$1::bigint",
      {task, code, message});
  db.exec("UPDATE task_attempts SET "
          "status='FAILED',finished_at=now(),error_code=$2,error_message=$3,"
          "exit_code=$4::integer,raw_upload_url='',upload_targets=" +
              std::string(terminal_log_grants_sql) +
              " WHERE "
              "id=$1::bigint",
          {attempt, code, message, std::to_string(exit_code)});
  event(db, task, attempt, "TASK_FAILED",
        {{"error_code", code}, {"message", message}});
}
} // namespace

class Services final : public dv::InitAgent::Service,
                       public dv::HealthCheck::Service,
                       public dv::Hotmethod::Service,
                       public dv::Control::Service {
public:
  explicit Services(Database &db)
      : db_(db),
        catalog_(secret("TASK_KINDS_FILE", "/etc/drop/task-kinds.json")),
        metrics_("drop-server", 9464) {
    const auto certificate = certificate_metadata(secret("TLS_CERT_FILE"));
    metrics_.set("drop_certificate_not_after_timestamp_seconds",
                 certificate.at("not_after_unix_seconds").get<double>());
  }

  grpc::Status Init(grpc::ServerContext *context, const dv::AgentInfo *request,
                    dv::InitResponse *response) override {
    return guarded([&] {
      require_agent(context, request->agent_id());
      if (request->protocol_version() != 1 && request->protocol_version() != 2)
        throw std::invalid_argument("Unsupported protocol version");
      json capabilities = json::array();
      for (const auto &capability : request->capabilities())
        capabilities.push_back(capability);
      auto labels = parse_json(
          request->labels_json().empty() ? "{}" : request->labels_json());
      if (!labels.is_object())
        throw std::invalid_argument("Agent labels must be an object");
      auto details = parse_json(request->capability_details_json().empty()
                                    ? "{}"
                                    : request->capability_details_json());
      if (!details.is_object() ||
          request->capability_details_json().size() > 65536 ||
          capabilities.size() > 64 || labels.dump().size() > 16384)
        throw std::invalid_argument("Agent registration exceeds limits");
      if (request->build_commit().size() > 128 || request->os().size() > 64 ||
          request->arch().size() > 64 || request->kernel().size() > 128 ||
          request->tool_versions_json().size() > 32768)
        throw std::invalid_argument("Registration metadata exceeds limits");
      auto tools = parse_json(request->tool_versions_json().empty()
                                  ? "{}"
                                  : request->tool_versions_json());
      if (!tools.is_object())
        throw std::invalid_argument("Tool versions must be an object");
      labels["registration"] = {
          {"protocol_version", request->protocol_version()},
          {"build_commit", request->build_commit()},
          {"os", request->os()},
          {"arch", request->arch()},
          {"kernel", request->kernel()},
          {"tool_versions", tools},
          {"certificate_expires_unix_ms",
           request->certificate_expires_unix_ms()}};
      std::lock_guard lock(db_.mutex);
      auto result =
          db_.exec("INSERT INTO "
                   "agents(id,hostname,ip,version,platform,capabilities,labels,"
                   "capability_details) "
                   "VALUES($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8::jsonb) ON "
                   "CONFLICT(id) DO UPDATE SET "
                   "hostname=EXCLUDED.hostname,ip=EXCLUDED.ip,version=EXCLUDED."
                   "version,platform=EXCLUDED.platform,capabilities=EXCLUDED."
                   "capabilities,labels=EXCLUDED.labels,capability_details="
                   "EXCLUDED.capability_details,last_seen_at=now(),status=CASE "
                   "WHEN agents.status='disabled' THEN 'disabled' ELSE "
                   "'online' END RETURNING status",
                   {request->agent_id(), request->hostname(), request->ip(),
                    request->version(), request->platform(),
                    capabilities.dump(), labels.dump(), details.dump()});
      if (result.get(0, "status") == "disabled")
        throw grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                           "Agent disabled");
      response->set_heartbeat_seconds(2);
      response->set_protocol_version(request->protocol_version());
      response->set_minimum_protocol_version(1);
      response->set_recommended_protocol_version(2);
      response->set_config_version(secret("AGENT_CONFIG_VERSION", "2"));
      if (request->protocol_version() == 1)
        response->set_upgrade_message(
            "Protocol 1 is supported as N-1; upgrade to 2 for typed targets "
            "and binary artifacts");
      metrics_.add("drop_agent_registrations_total");
    });
  }

  grpc::Status Do(grpc::ServerContext *context,
                  const dv::HeartbeatRequest *request,
                  dv::HeartbeatResponse *response) override {
    const auto started = unix_ns();
    return guarded([&] {
      require_agent(context, request->agent_id());
      auto resources = parse_json(
          request->resources_json().empty() ? "{}" : request->resources_json());
      if (!resources.is_object() ||
          request->resources_json().size() > 512 * 1024)
        throw std::invalid_argument("Invalid resources");
      std::lock_guard lock(db_.mutex);
      Transaction transaction(db_);
      auto found = db_.exec(
          "UPDATE agents SET "
          "last_seen_at=now(),status='online',cpu_percent=$2::float8,memory_"
          "percent=$3::float8,load_average=$4::float8,resources=$5::jsonb "
          "WHERE id=$1 AND status<>'disabled' RETURNING id",
          {request->agent_id(), std::to_string(request->cpu_percent()),
           std::to_string(request->memory_percent()),
           std::to_string(request->load_average()), resources.dump()});
      if (!found.count())
        throw grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                           "Agent is not registered or is disabled");
      db_.exec("INSERT INTO "
               "agent_samples(agent_id,cpu_percent,memory_percent,load_average)"
               " VALUES($1,$2::float8,$3::float8,$4::float8)",
               {request->agent_id(), std::to_string(request->cpu_percent()),
                std::to_string(request->memory_percent()),
                std::to_string(request->load_average())});
      if (request->events_size() > 32)
        throw std::invalid_argument("Too many heartbeat events");
      for (const auto &item : request->events())
        store_agent_event(item, request->agent_id());
      for (auto attempt : request->running_attempt_ids()) {
        auto owner = db_.exec("SELECT task_id FROM task_attempts WHERE "
                              "id=$1::bigint AND agent_id=$2",
                              {std::to_string(attempt), request->agent_id()});
        if (!owner.count()) {
          response->add_cancel_attempt_ids(attempt);
          continue;
        }
        // Lock the task before reading the latest attempt in a fresh statement.
        // A retry may have committed while this heartbeat waited for the lock.
        db_.exec("SELECT id FROM tasks WHERE id=$1::bigint FOR UPDATE",
                 {owner.get(0, "task_id")});
        auto rows =
            db_.exec("SELECT t.id,t.collection_status,a.id AS attempt_id FROM "
                     "task_attempts a JOIN tasks t ON t.id=a.task_id WHERE "
                     "a.id=$1::bigint AND a.agent_id=$2 AND a.attempt=(SELECT "
                     "max(x.attempt) FROM task_attempts x WHERE "
                     "x.task_id=t.id) FOR UPDATE OF t,a",
                     {std::to_string(attempt), request->agent_id()});
        if (!rows.count()) {
          response->add_cancel_attempt_ids(attempt);
          continue;
        }
        const auto state = rows.get(0, "collection_status");
        if (state == "CANCELED" || state == "FAILED" || state == "COLLECTED")
          response->add_cancel_attempt_ids(attempt);
        else
          db_.exec("UPDATE tasks SET updated_at=now() WHERE id=$1::bigint",
                   {rows.get(0, "id")});
      }
      if (request->running_attempt_ids().empty())
        dispatch(request->agent_id(), response);
      transaction.commit();
      trace_dispatched(*response, started);
      metrics_.add("drop_heartbeats_total");
    });
  }

  grpc::Status Collect(grpc::ServerContext *context,
                       const dv::CollectRequest *request,
                       dv::HeartbeatResponse *response) override {
    const auto started = unix_ns();
    return guarded([&] {
      require_agent(context, request->agent_id());
      std::lock_guard lock(db_.mutex);
      Transaction transaction(db_);
      dispatch(request->agent_id(), response);
      transaction.commit();
      trace_dispatched(*response, started);
    });
  }

  grpc::Status ReportProgress(grpc::ServerContext *context,
                              const dv::ProgressRequest *request,
                              dv::Ack *response) override {
    return guarded([&] {
      require_agent(context, request->agent_id());
      if (request->status() != "RUNNING" && request->status() != "UPLOADING")
        throw std::invalid_argument("Unsupported progress state");
      std::lock_guard lock(db_.mutex);
      Transaction transaction(db_);
      auto rows = current(request->task_id(), request->attempt_id(),
                          request->agent_id());
      auto old = rows.get(0, "collection_status");
      if (old == "CANCELED" || old == "FAILED")
        throw grpc::Status(grpc::StatusCode::CANCELLED, "Task is terminal");
      if (!collection_transition_allowed(old, request->status()))
        throw std::invalid_argument("Invalid collection transition");
      if (old != request->status()) {
        db_.exec("UPDATE tasks SET "
                 "collection_status=$2,updated_at=now(),version=version+1 "
                 "WHERE id=$1::bigint",
                 {std::to_string(request->task_id()), request->status()});
        db_.exec("UPDATE task_attempts SET "
                 "status=$2,started_at=COALESCE(started_at,now()) WHERE "
                 "id=$1::bigint",
                 {std::to_string(request->attempt_id()), request->status()});
        event(db_, std::to_string(request->task_id()),
              std::to_string(request->attempt_id()),
              request->status() == "RUNNING" ? "RUNNER_STARTED"
                                             : "RAW_ARTIFACT_UPLOADING");
      } else
        db_.exec("UPDATE tasks SET updated_at=now() WHERE id=$1::bigint",
                 {std::to_string(request->task_id())});
      transaction.commit();
      ack(response);
    });
  }

  grpc::Status NotifyResult(grpc::ServerContext *context,
                            const dv::ResultRequest *request,
                            dv::Ack *response) override {
    return guarded([&] {
      require_agent(context, request->agent_id());
      const auto task = std::to_string(request->task_id()),
                 attempt = std::to_string(request->attempt_id());
      std::lock_guard lock(db_.mutex);
      Transaction transaction(db_);
      auto rows = current(request->task_id(), request->attempt_id(),
                          request->agent_id());
      const auto state = rows.get(0, "collection_status");
      store_log_artifacts(rows, *request);
      if (state == "CANCELED" || state == "FAILED") {
        transaction.commit();
        ack(response);
        response->set_error_code("ATTEMPT_TERMINAL");
        return;
      }
      if (state == "COLLECTED") {
        auto existing =
            db_.exec("SELECT object_key,sha256,kind FROM artifacts WHERE "
                     "task_id=$1::bigint AND attempt_id=$2::bigint AND kind IN "
                     "('RAW','BINARY','LOG','MANIFEST')",
                     {task, attempt});
        if (!request->success() ||
            existing.count() != request->artifacts_size() + 1)
          throw std::invalid_argument(
              "Result replay differs from stored evidence");
        for (int i = 0; i < existing.count(); ++i) {
          bool match =
              existing.get(i, "kind") == "RAW" &&
              existing.get(i, "object_key") ==
                  request->artifact().object_key() &&
              existing.get(i, "sha256") == request->artifact().sha256();
          for (const auto &a : request->artifacts())
            match = match || (existing.get(i, "object_key") == a.object_key() &&
                              existing.get(i, "sha256") == a.sha256() &&
                              existing.get(i, "kind") == a.kind());
          if (!match)
            throw std::invalid_argument(
                "Result replay differs from stored evidence");
        }
        transaction.commit();
        ack(response);
        return;
      }
      if (!request->success()) {
        for (const auto &artifact : request->artifacts())
          if (artifact.kind() != "LOG")
            throw std::invalid_argument(
                "Failed collection may commit only diagnostics");
        metrics_.add("drop_collection_errors_total");
        fail(db_, task, attempt,
             request->error_code().empty()
                 ? "RUNNER_FAILED"
                 : request->error_code().substr(0, 64),
             request->error_message().substr(0, 1024), request->exit_code());
      } else {
        const auto &artifact = request->artifact();
        const auto &spec = catalog_.get(rows.get(0, "kind"));
        const auto params = parse_json(rows.get(0, "params"));
        const auto budget =
            std::min(spec.at("max_file_bytes").get<std::int64_t>(),
                     params.value("budget", json::object())
                         .value("max_file_bytes",
                                spec.at("max_file_bytes").get<std::int64_t>()));
        const auto raw_format =
            artifact.format().empty()
                ? (rows.get(0, "kind") == "PERF_CPU" &&
                           artifact.name() == "profile.folded"
                       ? "folded"
                       : spec.at("input_format").get<std::string>())
                : artifact.format();
        const auto formats =
            spec.value("input_formats", json::array({spec.at("input_format")}));
        const auto legacy_names =
            spec.value("legacy_raw_names", json::object());
        const auto raw_name = legacy_names.value(
            raw_format, spec.at("raw_name").get<std::string>());
        if (state != "UPLOADING" ||
            artifact.object_key() != rows.get(0, "raw_object_key") ||
            artifact.name() != raw_name ||
            artifact.content_type() !=
                spec.at("raw_content_type").get<std::string>() ||
            std::find(formats.begin(), formats.end(), raw_format) ==
                formats.end() ||
            artifact.size() <= 0 || artifact.size() > budget ||
            artifact.sha256().size() != 64 ||
            artifact.sha256().find_first_not_of("0123456789abcdef") !=
                std::string::npos)
          throw std::invalid_argument(
              "Invalid raw artifact manifest or collection state");
        store_artifact(task, attempt, artifact, "RAW", raw_format);
        const auto grants = parse_json(rows.get(0, "upload_targets"));
        if (request->artifacts_size() > 4)
          throw std::invalid_argument("Too many auxiliary artifacts");
        std::set<std::string> seen;
        for (const auto &auxiliary : request->artifacts()) {
          bool allowed = false;
          for (const auto &grant : grants)
            if (auxiliary.name() == grant.value("name", "") &&
                auxiliary.object_key() == grant.value("object_key", "") &&
                auxiliary.kind() == grant.value("kind", "") &&
                auxiliary.size() > 0 &&
                auxiliary.size() <= grant.value("max_file_bytes", 0LL) &&
                auxiliary.content_type() ==
                    grant.value("content_type", "application/octet-stream") &&
                auxiliary.format() == grant.value("format", "elf"))
              allowed = auxiliary.kind() != "MANIFEST" ||
                        grant.value("max_file_bytes", 0LL) == 65536;
          if (!allowed || !seen.insert(auxiliary.name()).second ||
              (auxiliary.kind() != "BINARY" && auxiliary.kind() != "LOG" &&
               auxiliary.kind() != "MANIFEST") ||
              auxiliary.sha256().size() != 64 ||
              auxiliary.sha256().find_first_not_of("0123456789abcdef") !=
                  std::string::npos)
            throw std::invalid_argument("Invalid auxiliary artifact manifest");
          if (auxiliary.kind() == "MANIFEST") {
            const auto key = "manifest/tasks/" + task + "/attempts/" +
                             rows.get(0, "attempt_number") +
                             "/collection-manifest.json";
            if (auxiliary.name() != "collection-manifest.json" ||
                auxiliary.object_key() != key ||
                auxiliary.format() != "collection_manifest_v1" ||
                auxiliary.content_type() != "application/json" ||
                auxiliary.size() > 65536)
              throw std::invalid_argument("Invalid fixed collection manifest");
          }
          if (auxiliary.kind() != "LOG")
            store_artifact(task, attempt, auxiliary, auxiliary.kind(),
                           auxiliary.format());
        }
        for (const auto &grant : grants)
          if (grant.value("kind", "") == "MANIFEST" &&
              grant.value("name", "") == "collection-manifest.json" &&
              !seen.contains("collection-manifest.json"))
            throw std::invalid_argument("Required collection manifest missing");
        for (const auto &expected :
             spec.value("auxiliary_artifacts", json::array()))
          if (expected.value("required", false) &&
              !seen.contains(expected.at("name").get<std::string>()))
            throw std::invalid_argument("Required auxiliary artifact missing");
        auto metadata = parse_json(request->runner_metadata_json().empty()
                                       ? "{}"
                                       : request->runner_metadata_json());
        if (!metadata.is_object() ||
            request->runner_metadata_json().size() > 32768)
          throw std::invalid_argument("Invalid runner metadata");
        db_.exec("UPDATE tasks SET "
                 "collection_status='COLLECTED',analysis_status='PENDING',"
                 "updated_at=now(),version=version+1,error_code=NULL,error_"
                 "message=NULL WHERE id=$1::bigint",
                 {task});
        db_.exec(
            "UPDATE task_attempts SET "
            "status='COLLECTED',finished_at=now(),exit_code=$2::integer,raw_"
            "upload_url='',upload_targets=" +
                std::string(terminal_log_grants_sql) +
                ",runner_metadata=$3::jsonb "
                "WHERE "
                "id=$1::bigint",
            {attempt, std::to_string(request->exit_code()), metadata.dump()});
        db_.exec(
            "INSERT INTO analysis_jobs(task_id,attempt_id,pipeline) "
            "VALUES($1::bigint,$2::bigint,$3) ON CONFLICT(task_id,attempt_id) "
            "DO NOTHING",
            {task, attempt, spec.at("analysis_pipeline").get<std::string>()});
        event(db_, task, attempt, "RAW_ARTIFACT_UPLOADED",
              {{"size", artifact.size()}, {"sha256", artifact.sha256()}});
        event(db_, task, attempt, "COLLECTION_SUCCEEDED");
      }
      transaction.commit();
      ack(response);
    });
  }

  grpc::Status CreateTask(grpc::ServerContext *context,
                          const dv::TaskDesc *request,
                          dv::Ack *response) override {
    const auto started = unix_ns();
    return guarded([&] {
      require_api(context);
      validate_priority(request->priority());
      std::lock_guard lock(db_.mutex);
      Transaction transaction(db_);
      // Queue admission has a separate per-Agent namespace. Taking the Agent
      // row after Task locks would invert Collect's Agent->Task lock order.
      db_.exec("SELECT pg_advisory_xact_lock(hashtextextended($1,41762032))",
               {request->target_agent_id()});
      auto rows = current(request->task_id(), request->attempt_id(),
                          request->target_agent_id());
      // Zero is also the N-1 API's absent-field default; persisted priority
      // remains authoritative even when a rollback client cannot express it.
      if (request->priority() != 0 &&
          request->priority() != std::stoi(rows.get(0, "priority")))
        throw std::invalid_argument(
            "Task priority differs from persisted task");
      const auto state = rows.get(0, "collection_status");
      if (state != "CREATED" && state != "QUEUED") {
        transaction.commit();
        ack(response);
        return;
      }
      auto params = parse_json(rows.get(0, "params"));
      catalog_.validate(rows.get(0, "kind"), params);
      if (rows.get(0, "raw_upload_url").empty())
        throw std::invalid_argument("Missing task-scoped upload URL");
      auto queue = db_.exec("SELECT count(*) AS count FROM tasks WHERE "
                            "target_agent_id=$1 AND collection_status='QUEUED'",
                            {request->target_agent_id()});
      if (std::stoi(queue.get(0, "count")) >= 32 && state != "QUEUED")
        throw grpc::Status(grpc::StatusCode::RESOURCE_EXHAUSTED,
                           "Agent queue full");
      if (state == "CREATED") {
        db_.exec("UPDATE tasks SET "
                 "collection_status='QUEUED',updated_at=now(),version=version+"
                 "1 WHERE id=$1::bigint",
                 {std::to_string(request->task_id())});
        event(db_, std::to_string(request->task_id()),
              std::to_string(request->attempt_id()), "DISPATCH_QUEUED");
      }
      transaction.commit();
      trace_committed("queue.task", rows.get(0, "request_id"),
                      request->task_id(), started,
                      params.value("traceparent", ""));
      ack(response);
    });
  }

  grpc::Status CancelTask(grpc::ServerContext *context,
                          const dv::TaskReference *request,
                          dv::Ack *response) override {
    return guarded([&] {
      require_api(context);
      if (request->attempt_id() <= 0)
        throw std::invalid_argument("Cancellation must specify an attempt");
      std::lock_guard lock(db_.mutex);
      Transaction transaction(db_);
      db_.exec("SELECT id FROM tasks WHERE id=$1::bigint FOR UPDATE",
               {std::to_string(request->task_id())});
      auto rows = db_.exec(
          "SELECT t.collection_status,a.id AS attempt_id FROM tasks t JOIN "
          "task_attempts a ON a.task_id=t.id WHERE t.id=$1::bigint ORDER BY "
          "a.attempt DESC LIMIT 1 FOR UPDATE OF t,a",
          {std::to_string(request->task_id())});
      if (!rows.count())
        throw grpc::Status(grpc::StatusCode::NOT_FOUND, "Task not found");
      if (!current_attempt_matches(request->attempt_id(),
                                   std::stoll(rows.get(0, "attempt_id")))) {
        transaction.commit();
        ack(response);
        return;
      }
      auto state = rows.get(0, "collection_status");
      if (state != "COLLECTED" && state != "FAILED" && state != "CANCELED") {
        db_.exec("UPDATE tasks SET "
                 "collection_status='CANCELED',analysis_status='CANCELED',"
                 "updated_at=now(),version=version+1 WHERE id=$1::bigint",
                 {std::to_string(request->task_id())});
        db_.exec("UPDATE task_attempts SET "
                 "status='CANCELED',finished_at=now(),raw_upload_url='',upload_"
                 "targets=" +
                     std::string(terminal_log_grants_sql) +
                     " WHERE id=$1::bigint",
                 {rows.get(0, "attempt_id")});
        event(db_, std::to_string(request->task_id()),
              rows.get(0, "attempt_id"), "TASK_CANCELED");
      }
      transaction.commit();
      ack(response);
    });
  }

  grpc::Status StatAgent(grpc::ServerContext *context,
                         const dv::AgentReference *request,
                         dv::AgentStats *response) override {
    return guarded([&] {
      require_api(context);
      std::lock_guard lock(db_.mutex);
      auto rows = db_.exec("SELECT resources FROM agents WHERE id=$1",
                           {request->agent_id()});
      if (!rows.count())
        throw grpc::Status(grpc::StatusCode::NOT_FOUND, "Agent not found");
      response->set_resources_json(rows.get(0, "resources"));
    });
  }

  grpc::Status Ping(grpc::ServerContext *context, const dv::Empty *,
                    dv::Ack *response) override {
    return guarded([&] {
      require_api(context);
      std::lock_guard lock(db_.mutex);
      db_.exec("SELECT 1");
      ack(response);
    });
  }

  grpc::Status ReportAgentEvent(grpc::ServerContext *context,
                                const dv::AgentEvent *request,
                                dv::Ack *response) override {
    return guarded([&] {
      require_agent(context, request->agent_id());
      std::lock_guard lock(db_.mutex);
      store_agent_event(*request, request->agent_id());
      ack(response);
    });
  }
  grpc::Status FetchData(grpc::ServerContext *context,
                         const dv::DataRequest *request,
                         dv::DataResponse *response) override {
    return guarded([&] {
      require_api(context);
      if (!safe_identifier(request->agent_id()))
        throw std::invalid_argument("Invalid agent identity");
      std::lock_guard lock(db_.mutex);
      auto rows = db_.exec(
          "SELECT resources,capability_details FROM agents WHERE id=$1",
          {request->agent_id()});
      if (!rows.count())
        throw grpc::Status(grpc::StatusCode::NOT_FOUND, "Agent not found");
      json data;
      if (request->category() == "resources")
        data = parse_json(rows.get(0, "resources"));
      else if (request->category() == "capabilities")
        data = parse_json(rows.get(0, "capability_details"));
      else if (request->category() == "processes") {
        data = parse_json(rows.get(0, "resources"))
                   .value("processes", json::array());
        const auto limit = static_cast<std::size_t>(
            std::clamp(request->limit() > 0 ? request->limit() : 200, 1, 2000));
        response->set_truncated(data.size() > limit);
        if (data.size() > limit)
          data.erase(data.begin() + static_cast<json::difference_type>(limit),
                     data.end());
      } else if (request->category() == "events") {
        const auto limit = std::clamp(request->limit(), 1, 100);
        auto events = db_.exec(
            "SELECT event_id,type,payload,(extract(epoch FROM "
            "occurred_at)*1000)::bigint AS time FROM agent_events WHERE "
            "agent_id=$1 ORDER BY occurred_at DESC LIMIT $2::integer",
            {request->agent_id(), std::to_string(limit + 1)});
        data = json::array();
        for (int i = 0; i < std::min(events.count(), limit); ++i)
          data.push_back(
              {{"event_id", events.get(i, "event_id")},
               {"type", events.get(i, "type")},
               {"payload", parse_json(events.get(i, "payload"))},
               {"occurred_at_unix_ms", std::stoll(events.get(i, "time"))}});
        response->set_truncated(events.count() > limit);
      } else
        throw std::invalid_argument("Unsupported data category");
      response->set_data_json(data.dump());
      response->set_generated_at_unix_ms(
          std::chrono::duration_cast<std::chrono::milliseconds>(
              std::chrono::system_clock::now().time_since_epoch())
              .count());
    });
  }

  void sweep() {
    std::lock_guard lock(db_.mutex);
    {
      Transaction offline(db_);
      db_.exec(
          "WITH changed AS (UPDATE agents SET status='offline' WHERE status "
          "NOT IN "
          "('disabled','offline') AND last_seen_at<now()-interval '15 seconds' "
          "RETURNING id,last_seen_at) "
          "INSERT INTO "
          "agent_events(agent_id,event_id,type,payload,occurred_at) SELECT id,"
          "'offline-'||md5(id||':'||last_seen_at::text),'AGENT_OFFLINE',"
          "jsonb_build_object('reason','heartbeat_timeout','last_seen_unix_ms',"
          "(extract(epoch FROM last_seen_at)*1000)::bigint),now() FROM changed "
          "ON CONFLICT(event_id) DO NOTHING");
      offline.commit();
    }
    Transaction transaction(db_);
    auto rows = db_.exec(
        "SELECT t.id,t.collection_status,a.id AS attempt_id,CASE WHEN "
        "t.collection_deadline<now() THEN 'TASK_TIMEOUT' ELSE "
        "'AGENT_DISCONNECTED' END AS code FROM tasks t JOIN task_attempts a ON "
        "a.task_id=t.id AND a.attempt=(SELECT max(x.attempt) FROM "
        "task_attempts x WHERE x.task_id=t.id) WHERE t.collection_status IN "
        "('CREATED','QUEUED','DELIVERED','RUNNING','UPLOADING') AND "
        "(t.collection_deadline<now() OR (t.collection_status IN "
        "('DELIVERED','RUNNING','UPLOADING') AND t.updated_at<now()-interval "
        "'45 seconds')) FOR UPDATE OF t,a SKIP LOCKED");
    for (int i = 0; i < rows.count(); ++i)
      fail(db_, rows.get(i, "id"), rows.get(i, "attempt_id"),
           rows.get(i, "code"),
           "Collection deadline or execution lease expired; retry explicitly "
           "to create a new attempt");
    metrics_.add("drop_collection_errors_total", rows.count());
    db_.exec(
        "DELETE FROM agent_samples WHERE created_at < now()-interval '7 days'");
    auto stats = db_.exec("SELECT count(*) FILTER(WHERE status='online') AS "
                          "online,COALESCE(max(extract(epoch FROM "
                          "now()-last_seen_at)),0) AS age FROM agents");
    metrics_.set("drop_agents_online", std::stod(stats.get(0, "online")));
    metrics_.set("drop_heartbeat_age_seconds_max",
                 std::stod(stats.get(0, "age")));
    auto queue = db_.exec(
        "SELECT count(*) AS count FROM tasks WHERE collection_status='QUEUED'");
    metrics_.set("drop_dispatch_queue_size", std::stod(queue.get(0, "count")));
    transaction.commit();
  }

private:
  void store_agent_event(const dv::AgentEvent &value,
                         const std::string &agent) {
    if (value.agent_id() != agent || !safe_identifier(value.event_id()) ||
        value.event_id().size() > 128 || !safe_identifier(value.type()) ||
        value.type().size() > 64 || value.payload_json().size() > 16384)
      throw std::invalid_argument("Invalid agent event");
    const auto now = std::chrono::duration_cast<std::chrono::milliseconds>(
                         std::chrono::system_clock::now().time_since_epoch())
                         .count();
    if (value.occurred_at_unix_ms() < now - 86400000LL ||
        value.occurred_at_unix_ms() > now + 300000LL)
      throw std::invalid_argument("Agent event timestamp out of range");
    auto payload =
        parse_json(value.payload_json().empty() ? "{}" : value.payload_json());
    if (!payload.is_object())
      throw std::invalid_argument("Invalid agent event payload");
    db_.exec(
        "INSERT INTO agent_events(agent_id,event_id,type,payload,occurred_at) "
        "VALUES($1,$2,$3,$4::jsonb,to_timestamp($5::double precision/1000)) ON "
        "CONFLICT(event_id) DO NOTHING",
        {agent, value.event_id(), value.type(), payload.dump(),
         std::to_string(value.occurred_at_unix_ms())});
  }
  void store_log_artifacts(const Rows &rows, const dv::ResultRequest &request) {
    if (request.artifacts_size() > 4)
      throw std::invalid_argument("Too many auxiliary artifacts");
    const auto grants = parse_json(rows.get(0, "upload_targets"));
    const auto state = rows.get(0, "collection_status");
    const bool terminal =
        state == "COLLECTED" || state == "CANCELED" || state == "FAILED";
    const auto expected_key = "raw/tasks/" + rows.get(0, "id") + "/attempts/" +
                              rows.get(0, "attempt_number") + "/runner.log";
    bool seen = false;
    for (const auto &artifact : request.artifacts()) {
      if (artifact.kind() != "LOG")
        continue;
      if (seen)
        throw std::invalid_argument("Duplicate diagnostic artifact");
      seen = true;
      if (artifact.name() != "runner.log" || artifact.format() != "log_jsonl" ||
          artifact.content_type() != "text/plain" ||
          artifact.object_key() != expected_key || artifact.size() <= 0 ||
          artifact.size() > 16384 || artifact.sha256().size() != 64 ||
          artifact.sha256().find_first_not_of("0123456789abcdef") !=
              std::string::npos)
        throw std::invalid_argument("Invalid bounded diagnostic manifest");
      const auto existing =
          db_.exec("SELECT sha256,size FROM artifacts WHERE task_id=$1::bigint "
                   "AND attempt_id=$2::bigint AND object_key=$3 AND kind='LOG'",
                   {rows.get(0, "id"), std::to_string(request.attempt_id()),
                    artifact.object_key()});
      if (existing.count()) {
        if (existing.get(0, "sha256") != artifact.sha256() ||
            std::stoll(existing.get(0, "size")) != artifact.size())
          throw std::invalid_argument(
              "Diagnostic replay differs from stored evidence");
        continue;
      }
      if (terminal) {
        const auto at = rows.get(0, "terminal_at_ms");
        const auto now =
            std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::system_clock::now().time_since_epoch())
                .count();
        if (at.empty() || now > std::stoll(at) + 120000)
          throw std::invalid_argument(
              "Terminal diagnostic upload grace expired");
      }
      bool allowed = false;
      for (const auto &grant : grants)
        if (grant.value("kind", "") == "LOG" &&
            grant.value("name", "") == "runner.log" &&
            grant.value("object_key", "") == artifact.object_key() &&
            grant.value("max_file_bytes", 0LL) == 16384 &&
            grant.value("format", "") == "log_jsonl" &&
            grant.value("content_type", "") == "text/plain")
          allowed = true;
      if (!allowed)
        throw std::invalid_argument("Diagnostic lacks current task grant");
      store_artifact(rows.get(0, "id"), std::to_string(request.attempt_id()),
                     artifact, "LOG", "log_jsonl");
      event(db_, rows.get(0, "id"), std::to_string(request.attempt_id()),
            "LOG_ARTIFACT_UPLOADED",
            {{"size", artifact.size()}, {"sha256", artifact.sha256()}});
    }
  }
  void store_artifact(const std::string &task, const std::string &attempt,
                      const dv::ArtifactInfo &artifact, const std::string &kind,
                      const std::string &format) {
    if (artifact.metadata_json().size() > 32768)
      throw std::invalid_argument("Artifact metadata exceeds limit");
    auto metadata = parse_json(
        artifact.metadata_json().empty() ? "{}" : artifact.metadata_json());
    if (!metadata.is_object())
      throw std::invalid_argument("Artifact metadata must be an object");
    db_.exec(
        "INSERT INTO "
        "artifacts(task_id,attempt_id,kind,name,object_key,size,sha256,content_"
        "type,format,metadata) "
        "VALUES($1::bigint,$2::bigint,$3,$4,$5,$6::bigint,$7,$8,$9,$10::jsonb)",
        {task, attempt, kind, artifact.name(), artifact.object_key(),
         std::to_string(artifact.size()), artifact.sha256(),
         artifact.content_type(), format, metadata.dump()});
  }
  Rows current(std::int64_t task, std::int64_t attempt,
               const std::string &agent) {
    db_.exec("SELECT id FROM tasks WHERE id=$1::bigint FOR UPDATE",
             {std::to_string(task)});
    auto rows = db_.exec(
        "SELECT "
        "t.id,t.kind,t.params,t.priority,t.request_id,t.collection_status,("
        "extract(epoch "
        "FROM COALESCE(a.finished_at,t.collection_deadline))*1000)::bigint AS "
        "terminal_at_ms,a.attempt AS attempt_number,a.raw_upload_url,a.raw_"
        "object_key,a.upload_targets FROM tasks t JOIN task_attempts a ON "
        "a.task_id=t.id WHERE t.id=$1::bigint AND a.id=$2::bigint AND "
        "a.agent_id=$3 AND a.attempt=(SELECT max(x.attempt) FROM task_attempts "
        "x WHERE x.task_id=t.id) FOR UPDATE OF t,a",
        {std::to_string(task), std::to_string(attempt), agent});
    if (!rows.count())
      throw grpc::Status(grpc::StatusCode::NOT_FOUND,
                         "Current task attempt not found");
    return rows;
  }
  void trace_committed(const std::string &operation,
                       const std::string &request_id, std::int64_t task,
                       std::int64_t started,
                       const std::string &parent) noexcept {
    try {
      metrics_.trace(operation, request_id, task, started, true, parent);
    } catch (...) {
      // Best-effort telemetry must never change an already committed RPC
      // result.
    }
  }
  void trace_dispatched(const dv::HeartbeatResponse &response,
                        std::int64_t started) {
    for (const auto &task : response.tasks())
      trace_committed("dispatch.task", task.request_id(), task.task_id(),
                      started, task.traceparent());
  }
  void dispatch(const std::string &agent, dv::HeartbeatResponse *response) {
    // Every dispatch entry point must serialize on the Agent row. Heartbeat
    // already owns this lock, while direct Collect may reach another Server
    // replica. Task SKIP LOCKED alone permits two different tasks to escape.
    auto registered = db_.exec(
        "SELECT id,labels FROM agents WHERE id=$1 AND status='online' AND "
        "last_seen_at>now()-interval '15 seconds' FOR UPDATE",
        {agent});
    if (!registered.count())
      return;
    auto active = db_.exec(
        "SELECT id FROM tasks WHERE target_agent_id=$1 AND collection_status "
        "IN ('DELIVERED','RUNNING','UPLOADING') LIMIT 1",
        {agent});
    if (active.count())
      return;
    auto rows = db_.exec(
        "SELECT t.id,t.kind,t.params,t.priority,t.request_id,(extract(epoch "
        "FROM "
        "t.collection_deadline)*1000)::bigint AS deadline,a.id AS "
        "attempt_id,a.raw_upload_url,a.raw_object_key,a.upload_targets FROM "
        "tasks t JOIN task_attempts a ON a.task_id=t.id AND a.attempt=(SELECT "
        "max(x.attempt) FROM task_attempts x WHERE x.task_id=t.id) WHERE "
        "t.target_agent_id=$1 AND t.collection_status='QUEUED' AND "
        "t.collection_deadline>now() AND a.raw_upload_url<>'' ORDER BY "
        "t.priority DESC,t.created_at,t.id LIMIT 1 FOR UPDATE OF t,a SKIP "
        "LOCKED",
        {agent});
    if (!rows.count())
      return;
    const auto task = rows.get(0, "id"), attempt = rows.get(0, "attempt_id");
    auto parameters = parse_json(rows.get(0, "params"));
    auto *desc = response->add_tasks();
    desc->set_task_id(std::stoll(task));
    desc->set_attempt_id(std::stoll(attempt));
    desc->set_priority(std::stoi(rows.get(0, "priority")));
    desc->set_kind(rows.get(0, "kind"));
    desc->set_target_agent_id(agent);
    desc->set_deadline_unix_ms(std::stoll(rows.get(0, "deadline")));
    desc->set_raw_upload_url(rows.get(0, "raw_upload_url"));
    desc->set_raw_object_key(rows.get(0, "raw_object_key"));
    const auto &spec = catalog_.get(rows.get(0, "kind"));
    catalog_.validate(rows.get(0, "kind"), parameters);
    const auto defaults = spec;
    const auto options = parameters.value("options", json::object());
    const auto budget = parameters.value("budget", json::object());
    desc->set_request_id(rows.get(0, "request_id"));
    desc->set_max_file_bytes(
        std::min(budget.value("max_file_bytes",
                              spec.at("max_file_bytes").get<std::int64_t>()),
                 spec.at("max_file_bytes").get<std::int64_t>()));
    desc->set_protocol_version(2);
    desc->set_extra_parameters_json(options.dump());
    desc->set_raw_name(spec.at("raw_name"));
    desc->set_raw_format(spec.at("input_format"));
    desc->set_raw_content_type(spec.at("raw_content_type"));
    desc->set_analysis_pipeline(spec.at("analysis_pipeline"));
    desc->set_traceparent(parameters.value("traceparent", ""));
    auto target =
        parameters.value("target", json{{"type", "process"},
                                        {"pid", parameters.value("pid", 0)}});
    auto *typed = desc->mutable_target();
    typed->set_type(target.value("type", "process"));
    typed->set_pid(target.value("pid", parameters.value("pid", 0)));
    typed->set_start_time_ticks(target.value("start_time_ticks", 0ULL));
    typed->set_container_id(target.value("container_id", ""));
    typed->set_runtime(target.value("runtime", "docker"));
    typed->set_container_pid(target.value("container_pid", 0));
    typed->set_expected_uid(target.value("expected_uid", 0U));
    typed->set_check_uid(target.value("check_uid", false));
    auto *limits = desc->mutable_budget();
    limits->set_max_file_bytes(desc->max_file_bytes());
    limits->set_max_address_space_bytes(
        budget.value("max_address_space_bytes", 8LL * 1024 * 1024 * 1024));
    limits->set_max_cpu_seconds(budget.value("max_cpu_seconds", 120));
    limits->set_max_processes(budget.value("max_processes", 32));
    limits->set_max_concurrency(1);
    auto granted_targets = parse_json(rows.get(0, "upload_targets"));
    if (parse_json(registered.get(0, "labels"))
            .value("collection_manifest_version", 0) != 1) {
      for (auto item = granted_targets.begin(); item != granted_targets.end();)
        if (item->value("kind", "") == "MANIFEST")
          item = granted_targets.erase(item);
        else
          ++item;
      db_.exec("UPDATE task_attempts SET upload_targets=$2::jsonb WHERE "
               "id=$1::bigint",
               {attempt, granted_targets.dump()});
    }
    for (const auto &grant : granted_targets) {
      auto *upload = desc->add_upload_targets();
      upload->set_name(grant.at("name"));
      upload->set_object_key(grant.at("object_key"));
      upload->set_put_url(grant.at("put_url"));
      upload->set_kind(grant.at("kind"));
      upload->set_max_file_bytes(grant.at("max_file_bytes"));
    }
    auto *params = desc->mutable_parameters();
    params->set_pid(parameters.value("pid", typed->pid()));
    params->set_duration_seconds(parameters.value(
        "duration_seconds", defaults.value("default_duration_seconds", 10)));
    params->set_frequency_hz(parameters.value(
        "frequency_hz", defaults.value("default_frequency_hz", 99)));
    params->set_scope(parameters.value("scope", "process"));
    const auto kind = rows.get(0, "kind");
    if (kind == "PERF_CPU") {
      desc->mutable_perf()->set_event(options.value("event", "cpu-clock"));
      desc->mutable_perf()->set_call_graph(options.value("call_graph", "fp"));
    } else if (kind == "JAVA_CPU") {
      desc->mutable_java_profiler()->set_event(options.value("event", "cpu"));
      desc->mutable_java_profiler()->set_output_format("collapsed");
    } else if (kind == "GO_CPU" || kind == "GO_HEAP") {
      desc->mutable_go_profile()->set_port(options.value("port", 6060));
      desc->mutable_go_profile()->set_path(
          options.value("path", kind == "GO_CPU" ? "/debug/pprof/profile"
                                                 : "/debug/pprof/heap"));
      desc->mutable_go_profile()->set_profile_type(kind == "GO_CPU" ? "cpu"
                                                                    : "heap");
    } else if (kind == "PYTHON_CPU" || kind == "PYTHON_MEMORY") {
      desc->mutable_python_profiler()->set_mode(
          kind == "PYTHON_CPU" ? "cpu" : "memory");
      desc->mutable_python_profiler()->set_native(
          options.value("native", false));
    } else if (kind == "JAVA_HEAP") {
      desc->mutable_heap()->set_format("hprof");
      desc->mutable_heap()->set_live(options.value("live", true));
    } else if (kind == "IO_TRACE" || kind == "MEMORY_TRACE") {
      desc->mutable_trace()->set_category(options.value(
          "category", kind == "IO_TRACE" ? "read_write" : "alloc"));
      desc->mutable_trace()->set_min_duration_us(
          options.value("min_duration_us", 0));
    } else if (kind == "GPERF_CPU" || kind == "GPERF_HEAP" ||
               kind == "JEPROF_HEAP") {
      desc->mutable_allocator()->set_mode(kind == "GPERF_CPU"    ? "cpu"
                                          : kind == "GPERF_HEAP" ? "heap"
                                                                 : "jemalloc");
    } else if (kind == "BOLT_PROFILE") {
      desc->mutable_bolt()->set_binary_path(
          "/proc/" + std::to_string(typed->pid()) + "/exe");
      desc->mutable_bolt()->set_optimize(options.value("optimize", false));
    } else if (kind == "SCRIPT")
      desc->mutable_script()->set_template_id(
          options.value("template_id", "process_status"));
    else
      desc->mutable_native()->set_category(kind);
    db_.exec("UPDATE tasks SET "
             "collection_status='DELIVERED',updated_at=now(),version=version+1 "
             "WHERE id=$1::bigint",
             {task});
    db_.exec("UPDATE task_attempts SET status='DELIVERED' WHERE id=$1::bigint",
             {attempt});
    event(db_, task, attempt, "TASK_DELIVERED");
    metrics_.add("drop_dispatch_total");
  }
  Database &db_;
  Catalog catalog_;
  Telemetry metrics_;
};
} // namespace dropcore

int main(int argc, char **argv) {
  using namespace dropcore;
  try {
    initialize_configuration(argc, argv);
    Database db(secret("DATABASE_URL"));
    if (db.exec("SELECT version FROM schema_migrations WHERE version=5")
            .count() != 1)
      throw std::runtime_error("Collection schema migration 5 is required");
    Services services(db);
    grpc::SslServerCredentialsOptions options;
    options.pem_root_certs = read_file(secret("TLS_CA_FILE"));
    options.pem_key_cert_pairs.push_back({read_file(secret("TLS_KEY_FILE")),
                                          read_file(secret("TLS_CERT_FILE"))});
    options.client_certificate_request =
        GRPC_SSL_REQUEST_AND_REQUIRE_CLIENT_CERTIFICATE_AND_VERIFY;
    grpc::ServerBuilder builder;
    builder.SetMaxReceiveMessageSize(1024 * 1024);
    builder.AddListeningPort(secret("GRPC_LISTEN", "0.0.0.0:50051"),
                             grpc::SslServerCredentials(options));
    builder.RegisterService(static_cast<dv::InitAgent::Service *>(&services));
    builder.RegisterService(static_cast<dv::HealthCheck::Service *>(&services));
    builder.RegisterService(static_cast<dv::Hotmethod::Service *>(&services));
    builder.RegisterService(static_cast<dv::Control::Service *>(&services));
    auto server = builder.BuildAndStart();
    if (!server)
      throw std::runtime_error("gRPC listener failed");
    log_configuration("drop-server");
    log("drop-server", "mTLS control plane ready");
    std::jthread sweeper([&](std::stop_token stop) {
      while (!stop.stop_requested()) {
        try {
          services.sweep();
        } catch (...) {
          log("drop-server", "Reconciliation deferred", "", "DB_UNAVAILABLE");
        }
        std::this_thread::sleep_for(std::chrono::seconds(5));
      }
    });
    server->Wait();
  } catch (const std::exception &) {
    log("drop-server", "Startup failed; check database and TLS files", "",
        "CONFIGURATION_ERROR");
    return 1;
  }
}
