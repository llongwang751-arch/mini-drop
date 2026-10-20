#include "drop.grpc.pb.h"
#include "drop/database.hpp"
#include "drop/process.hpp"

#include <barrier>
#include <chrono>
#include <future>
#include <grpcpp/grpcpp.h>
#include <iostream>
#include <memory>
#include <nlohmann/json.hpp>
#include <stdexcept>
#include <thread>
#include <unistd.h>

namespace dv = drop::v1;
using namespace dropcore;
namespace {
void require(bool condition, const char *message) {
  if (!condition)
    throw std::runtime_error(message);
}
void timeout(grpc::ClientContext &context) {
  context.set_deadline(std::chrono::system_clock::now() +
                       std::chrono::seconds(5));
}
std::shared_ptr<grpc::Channel> channel(const char *cert, const char *key,
                                       std::string address = {}) {
  grpc::SslCredentialsOptions tls;
  tls.pem_root_certs = read_file(secret("TLS_CA_FILE"));
  tls.pem_cert_chain = read_file(secret(cert));
  tls.pem_private_key = read_file(secret(key));
  grpc::ChannelArguments arguments;
  if (address.empty()) {
    address = secret("DROP_SERVER_ADDRESS", "drop-server:50051");
  } else {
    // Both isolated Server replicas use the same explicitly mounted identity.
    arguments.SetSslTargetNameOverride("drop-server");
  }
  return grpc::CreateCustomChannel(address, grpc::SslCredentials(tls),
                                   arguments);
}
} // namespace

// Explicit operational regression probe. It creates and removes its own scoped
// database fixtures, and is deliberately not part of default offline CTest.
int main() {
  std::string task_id, user_id = "core-probe-" + std::to_string(getpid());
  std::unique_ptr<Database> db;
  auto cleanup = [&] {
    if (!db)
      return;
    db->exec("DROP TRIGGER IF EXISTS drop_rpc_fixture_delay ON tasks");
    db->exec("DROP FUNCTION IF EXISTS drop_rpc_fixture_delay()");
    const auto owned =
        db->exec("SELECT id FROM tasks WHERE creator_id=$1", {user_id});
    for (int index = 0; index < owned.count(); ++index) {
      const auto id = owned.get(index, "id");
      for (const auto *table : {"analysis_jobs", "artifacts", "task_events",
                                "outbox", "task_attempts"}) {
        db->exec(std::string("DELETE FROM ") + table +
                     " WHERE task_id=$1::bigint",
                 {id});
      }
      db->exec("DELETE FROM tasks WHERE id=$1::bigint", {id});
    }
    db->exec("DELETE FROM users WHERE id=$1", {user_id});
  };
  try {
    if (secret("ALLOW_ISOLATED_RPC_FIXTURE") != "true")
      throw std::runtime_error(
          "RPC regression requires an isolated test database and explicit "
          "ALLOW_ISOLATED_RPC_FIXTURE=true");
    db = std::make_unique<Database>(secret("DATABASE_URL"));
    auto api = channel("TEST_API_CERT_FILE", "TEST_API_KEY_FILE");
    auto agent = channel("TEST_AGENT_CERT_FILE", "TEST_AGENT_KEY_FILE");
    auto control = dv::Control::NewStub(api);
    auto forbidden_control = dv::Control::NewStub(agent);
    auto forbidden_init = dv::InitAgent::NewStub(api);
    dv::Ack response;
    dv::Empty empty;
    {
      grpc::ClientContext context;
      timeout(context);
      require(control->Ping(&context, empty, &response).ok(),
              "API mTLS Ping failed");
    }
    {
      grpc::ClientContext context;
      timeout(context);
      require(
          forbidden_control->Ping(&context, empty, &response).error_code() ==
              grpc::StatusCode::PERMISSION_DENIED,
          "Agent certificate gained API control access");
    }
    {
      grpc::ClientContext context;
      timeout(context);
      dv::AgentInfo request;
      request.set_agent_id(secret("AGENT_ID", "aliyun-local"));
      dv::InitResponse reply;
      require(forbidden_init->Init(&context, request, &reply).error_code() ==
                  grpc::StatusCode::PERMISSION_DENIED,
              "API certificate gained Agent identity");
    }
    const auto agent_id = secret("AGENT_ID", "aliyun-local");
    auto registration = dv::InitAgent::NewStub(agent);
    for (const auto version : {1, 2}) {
      dv::AgentInfo info;
      info.set_agent_id(agent_id);
      info.set_hostname("isolated-protocol-fixture");
      info.set_protocol_version(version);
      info.set_os("Linux");
      info.set_arch("x86_64");
      info.set_kernel("fixture");
      info.set_build_commit("fixture");
      info.set_tool_versions_json("{}");
      info.add_capabilities("PROC_CPU");
      dv::InitResponse reply;
      grpc::ClientContext context;
      timeout(context);
      require(registration->Init(&context, info, &reply).ok(),
              "N/N-1 registration failed");
      require(reply.protocol_version() == version &&
                  reply.minimum_protocol_version() == 1 &&
                  reply.recommended_protocol_version() == 2 &&
                  !reply.config_version().empty(),
              "Protocol negotiation metadata missing");
      if (version == 1)
        require(!reply.upgrade_message().empty(), "N-1 upgrade hint missing");
    }
    {
      dv::AgentInfo info;
      info.set_agent_id(agent_id);
      info.set_protocol_version(3);
      dv::InitResponse reply;
      grpc::ClientContext context;
      timeout(context);
      require(registration->Init(&context, info, &reply).error_code() ==
                  grpc::StatusCode::INVALID_ARGUMENT,
              "Incompatible major version accepted");
    }
    auto data = dv::Control::NewStub(api);
    dv::DataRequest query;
    query.set_agent_id(agent_id);
    query.set_category("capabilities");
    dv::DataResponse data_reply;
    {
      grpc::ClientContext context;
      timeout(context);
      require(data->FetchData(&context, query, &data_reply).ok(),
              "API FetchData failed");
    }
    {
      grpc::ClientContext context;
      timeout(context);
      require(forbidden_control->FetchData(&context, query, &data_reply)
                      .error_code() == grpc::StatusCode::PERMISSION_DENIED,
              "Agent gained control FetchData access");
    }
    auto hot_events = dv::Hotmethod::NewStub(agent);
    dv::AgentEvent agent_event;
    agent_event.set_agent_id(agent_id);
    agent_event.set_event_id(user_id + "-registered");
    agent_event.set_type("FIXTURE_EVENT");
    agent_event.set_payload_json("{\"fixture\":true}");
    agent_event.set_occurred_at_unix_ms(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch())
            .count());
    for (int i = 0; i < 2; ++i) {
      grpc::ClientContext context;
      timeout(context);
      require(
          hot_events->ReportAgentEvent(&context, agent_event, &response).ok(),
          "Agent event failed");
    }
    auto event_count =
        db->exec("SELECT count(*) AS count FROM agent_events WHERE event_id=$1",
                 {agent_event.event_id()});
    require(event_count.get(0, "count") == "1",
            "Agent event deduplication failed");
    db->exec("DELETE FROM agent_events WHERE event_id=$1",
             {agent_event.event_id()});
    db->exec("UPDATE agents SET status='disabled' WHERE id=$1", {agent_id});
    {
      dv::AgentInfo info;
      info.set_agent_id(agent_id);
      info.set_protocol_version(2);
      dv::InitResponse reply;
      grpc::ClientContext context;
      timeout(context);
      require(registration->Init(&context, info, &reply).error_code() ==
                  grpc::StatusCode::PERMISSION_DENIED,
              "Disabled agent re-enabled itself");
    }
    require(db->exec("SELECT status FROM agents WHERE id=$1", {agent_id})
                    .get(0, "status") == "disabled",
            "Disabled state was overwritten");
    db->exec("UPDATE agents SET status='online' WHERE id=$1", {agent_id});
    if (secret("TEST_ONLY_DISPATCH_RACE") != "true") {
      // The two sweepers must commit one transition and one event, even when
      // both observe the same expired heartbeat. Re-registration starts a new
      // outage, rather than reusing or multiplying the preceding event.
      db->exec("DELETE FROM agent_events WHERE agent_id=$1", {agent_id});
      for (int outage = 1; outage <= 2; ++outage) {
        db->exec(
            "UPDATE agents SET status='online',last_seen_at=now()-interval "
            "'20 seconds' WHERE id=$1",
            {agent_id});
        bool observed = false;
        for (int poll = 0; poll < 30; ++poll) {
          const auto state = db->exec(
              "SELECT status,(SELECT count(*) FROM agent_events WHERE "
              "agent_id=$1 AND type='AGENT_OFFLINE') AS events FROM agents "
              "WHERE id=$1",
              {agent_id});
          if (state.get(0, "status") == "offline" &&
              state.get(0, "events") == std::to_string(outage)) {
            observed = true;
            break;
          }
          std::this_thread::sleep_for(std::chrono::milliseconds(250));
        }
        require(observed, "Offline transition and event were not atomic");
        std::this_thread::sleep_for(std::chrono::seconds(6));
        require(db->exec("SELECT count(*) AS count FROM agent_events WHERE "
                         "agent_id=$1 AND type='AGENT_OFFLINE'",
                         {agent_id})
                        .get(0, "count") == std::to_string(outage),
                "Repeated or cross-Server sweep duplicated offline event");
        dv::AgentInfo info;
        info.set_agent_id(agent_id);
        info.set_protocol_version(2);
        info.add_capabilities("PROC_CPU");
        grpc::ClientContext context;
        timeout(context);
        dv::InitResponse reply;
        require(registration->Init(&context, info, &reply).ok(),
                "Agent could not recover from offline state");
      }
    }
    Transaction fixture(*db);
    db->exec("INSERT INTO users(id,username,display_name,role) "
             "VALUES($1,$1,'Core regression fixture','viewer')",
             {user_id});
    auto task = db->exec(
        "INSERT INTO "
        "tasks(name,kind,target_agent_id,creator_id,request_id,collection_"
        "status,collection_deadline,updated_at) VALUES('Core cancellation "
        "regression','PROC_CPU',$1,$2,$2,'CREATED',now()+interval '10 "
        "minutes',now()-interval '20 seconds') RETURNING id",
        {agent_id, user_id});
    task_id = task.get(0, "id");
    auto old =
        db->exec("INSERT INTO "
                 "task_attempts(task_id,attempt,agent_id,status,raw_object_key)"
                 " VALUES($1::bigint,1,$2,'FAILED',$3) RETURNING id",
                 {task_id, agent_id, "probe/" + user_id + "/1"});
    auto current =
        db->exec("INSERT INTO "
                 "task_attempts(task_id,attempt,agent_id,status,raw_object_key)"
                 " VALUES($1::bigint,2,$2,'QUEUED',$3) RETURNING id",
                 {task_id, agent_id, "probe/" + user_id + "/2"});
    fixture.commit();
    const auto old_id = std::stoll(old.get(0, "id")),
               current_id = std::stoll(current.get(0, "id"));
    dv::TaskReference cancellation;
    cancellation.set_task_id(std::stoll(task_id));
    cancellation.set_attempt_id(old_id);
    {
      grpc::ClientContext context;
      timeout(context);
      require(control->CancelTask(&context, cancellation, &response).ok(),
              "Stale cancel RPC failed");
    }
    auto before = db->exec("SELECT collection_status,updated_at::text FROM "
                           "tasks WHERE id=$1::bigint",
                           {task_id});
    require(before.get(0, "collection_status") == "CREATED",
            "Stale cancellation modified retry");
    auto health = dv::HealthCheck::NewStub(agent);
    dv::HeartbeatRequest heartbeat;
    heartbeat.set_agent_id(agent_id);
    heartbeat.set_resources_json("{}");
    heartbeat.add_running_attempt_ids(old_id);
    dv::HeartbeatResponse heartbeat_response;
    {
      grpc::ClientContext context;
      timeout(context);
      require(health->Do(&context, heartbeat, &heartbeat_response).ok(),
              "Stale attempt heartbeat failed");
    }
    require(heartbeat_response.cancel_attempt_ids_size() == 1 &&
                heartbeat_response.cancel_attempt_ids(0) == old_id,
            "Old runner did not receive cancellation");
    auto after = db->exec(
        "SELECT updated_at::text FROM tasks WHERE id=$1::bigint", {task_id});
    require(after.get(0, "updated_at") == before.get(0, "updated_at"),
            "Old heartbeat renewed retry execution lease");
    auto hot = dv::Hotmethod::NewStub(agent);
    dv::ProgressRequest stale_progress;
    stale_progress.set_agent_id(agent_id);
    stale_progress.set_task_id(std::stoll(task_id));
    stale_progress.set_attempt_id(old_id);
    stale_progress.set_status("RUNNING");
    {
      grpc::ClientContext context;
      timeout(context);
      require(hot->ReportProgress(&context, stale_progress, &response)
                      .error_code() == grpc::StatusCode::NOT_FOUND,
              "Stale progress reached current attempt");
    }
    dv::ResultRequest stale_result;
    stale_result.set_agent_id(agent_id);
    stale_result.set_task_id(std::stoll(task_id));
    stale_result.set_attempt_id(old_id);
    stale_result.set_success(false);
    stale_result.set_error_code("AGENT_RESTARTED");
    {
      grpc::ClientContext context;
      timeout(context);
      require(
          hot->NotifyResult(&context, stale_result, &response).error_code() ==
              grpc::StatusCode::NOT_FOUND,
          "Stale failure reached current attempt");
    }
    cancellation.set_attempt_id(current_id);
    {
      grpc::ClientContext context;
      timeout(context);
      require(control->CancelTask(&context, cancellation, &response).ok(),
              "Current cancel RPC failed");
    }
    auto canceled = db->exec(
        "SELECT collection_status FROM tasks WHERE id=$1::bigint", {task_id});
    require(canceled.get(0, "collection_status") == "CANCELED",
            "Current attempt was not canceled");
    db->exec("UPDATE tasks SET "
             "kind='GO_HEAP',params='{\"pid\":123,\"duration_seconds\":1}',"
             "collection_status='UPLOADING',analysis_status='PENDING' WHERE "
             "id=$1::bigint",
             {task_id});
    db->exec("UPDATE task_attempts SET status='UPLOADING' WHERE id=$1::bigint",
             {std::to_string(current_id)});
    dv::ResultRequest binary_result;
    binary_result.set_agent_id(agent_id);
    binary_result.set_task_id(std::stoll(task_id));
    binary_result.set_attempt_id(current_id);
    binary_result.set_success(true);
    auto *manifest = binary_result.mutable_artifact();
    manifest->set_name("heap.pprof");
    manifest->set_format("pprof");
    manifest->set_content_type("application/octet-stream");
    manifest->set_size(123);
    manifest->set_sha256(std::string(64, 'a'));
    manifest->set_object_key("probe/" + user_id + "/2");
    manifest->set_metadata_json("{\"fixture\":true}");
    {
      manifest->set_format("folded");
      grpc::ClientContext context;
      timeout(context);
      require(
          hot->NotifyResult(&context, binary_result, &response).error_code() ==
              grpc::StatusCode::INVALID_ARGUMENT,
          "Mismatched raw format accepted");
    }
    require(db->exec("SELECT count(*) AS count FROM artifacts WHERE "
                     "task_id=$1::bigint",
                     {task_id})
                    .get(0, "count") == "0",
            "Rejected manifest partially persisted");
    manifest->set_format("pprof");
    {
      grpc::ClientContext context;
      timeout(context);
      require(hot->NotifyResult(&context, binary_result, &response).ok(),
              "Binary raw manifest rejected");
    }
    require(
        db->exec("SELECT pipeline FROM analysis_jobs WHERE task_id=$1::bigint",
                 {task_id})
                .get(0, "pipeline") == "pprof_heap_v1",
        "Wrong task-kind pipeline persisted");
    require(db->exec("SELECT format FROM artifacts WHERE task_id=$1::bigint",
                     {task_id})
                    .get(0, "format") == "pprof",
            "Native format not persisted");
    {
      grpc::ClientContext context;
      timeout(context);
      require(hot->NotifyResult(&context, binary_result, &response).ok(),
              "Identical result replay failed");
    }
    manifest->set_sha256(std::string(64, 'b'));
    {
      grpc::ClientContext context;
      timeout(context);
      require(
          hot->NotifyResult(&context, binary_result, &response).error_code() ==
              grpc::StatusCode::INVALID_ARGUMENT,
          "Conflicting result replay accepted");
    }
    db->exec("DELETE FROM analysis_jobs WHERE task_id=$1::bigint", {task_id});
    db->exec("DELETE FROM artifacts WHERE task_id=$1::bigint", {task_id});
    const bool dispatch_only = secret("TEST_ONLY_DISPATCH_RACE") == "true";
    if (!dispatch_only) {
      // A grant requires the new artifact; old Agents have no such grant and
      // remain valid. Failed validation must roll back RAW and job insertion.
      const auto key =
          "manifest/tasks/" + task_id + "/attempts/2/collection-manifest.json";
      const auto manifest_grants =
          nlohmann::json::array({{{"name", "collection-manifest.json"},
                                  {"kind", "MANIFEST"},
                                  {"object_key", key},
                                  {"put_url", "https://fixture.invalid/unused"},
                                  {"max_file_bytes", 65536},
                                  {"format", "collection_manifest_v1"},
                                  {"content_type", "application/json"}}});
      db->exec(
          "UPDATE tasks SET collection_status='UPLOADING' WHERE id=$1::bigint",
          {task_id});
      db->exec("UPDATE task_attempts SET status='UPLOADING',finished_at=NULL,"
               "upload_targets=$2::jsonb WHERE id=$1::bigint",
               {std::to_string(current_id), manifest_grants.dump()});
      for (int invalid = 0; invalid < 5; ++invalid) {
        auto request = binary_result;
        if (invalid != 0) {
          auto *auxiliary = request.add_artifacts();
          auxiliary->set_kind("MANIFEST");
          auxiliary->set_name("collection-manifest.json");
          auxiliary->set_format("collection_manifest_v1");
          auxiliary->set_content_type("application/json");
          auxiliary->set_object_key(invalid == 1 ? "raw/tasks/invalid" : key);
          auxiliary->set_size(invalid == 2 ? 65537 : 321);
          auxiliary->set_sha256(invalid == 3 ? "invalid"
                                             : std::string(64, 'c'));
          if (invalid == 4)
            *request.add_artifacts() = *auxiliary;
        }
        grpc::ClientContext context;
        timeout(context);
        require(hot->NotifyResult(&context, request, &response).error_code() ==
                    grpc::StatusCode::INVALID_ARGUMENT,
                "Missing, invalid or duplicate collection manifest accepted");
        require(
            db->exec("SELECT (SELECT count(*) FROM artifacts WHERE "
                     "task_id=$1::bigint)+(SELECT count(*) FROM analysis_jobs "
                     "WHERE task_id=$1::bigint) AS count",
                     {task_id})
                    .get(0, "count") == "0",
            "Rejected collection manifest partially persisted artifacts/job");
      }
      auto *auxiliary = binary_result.add_artifacts();
      auxiliary->set_kind("MANIFEST");
      auxiliary->set_name("collection-manifest.json");
      auxiliary->set_format("collection_manifest_v1");
      auxiliary->set_content_type("application/json");
      auxiliary->set_object_key(key);
      auxiliary->set_size(321);
      auxiliary->set_sha256(std::string(64, 'c'));
      for (int replay = 0; replay < 2; ++replay) {
        grpc::ClientContext context;
        timeout(context);
        require(hot->NotifyResult(&context, binary_result, &response).ok(),
                "Authorized collection manifest or identical replay rejected");
      }
      require(db->exec("SELECT count(*) AS count FROM artifacts WHERE "
                       "task_id=$1::bigint AND kind='MANIFEST'",
                       {task_id})
                      .get(0, "count") == "1",
              "Collection manifest was not persisted once");
      require(db->exec("SELECT upload_targets='[]'::jsonb AS revoked FROM "
                       "task_attempts WHERE id=$1::bigint",
                       {std::to_string(current_id)})
                      .get(0, "revoked") == "t",
              "Terminal collection retained manifest PUT authorization");
      db->exec("DELETE FROM analysis_jobs WHERE task_id=$1::bigint", {task_id});
      db->exec("DELETE FROM artifacts WHERE task_id=$1::bigint", {task_id});
      binary_result.clear_artifacts();
      // This synthetic manifest fixture tests control-plane authorization. The
      // actual Agent/PUT fixture separately verifies uploaded bytes and SHA.
      const auto log_key = "raw/tasks/" + task_id + "/attempts/2/runner.log";
      const auto grants =
          "[{\"name\":\"runner.log\",\"kind\":\"LOG\",\"object_key\":\"" +
          log_key +
          "\",\"put_url\":\"http://private-fixture.invalid/"
          "unused\",\"max_file_bytes\":16384,\"format\":\"log_jsonl\","
          "\"content_type\":\"text/"
          "plain\",\"unknown_url\":\"must-be-discarded\"}]";
      db->exec(
          "UPDATE tasks SET collection_status='RUNNING' WHERE id=$1::bigint",
          {task_id});
      db->exec("UPDATE task_attempts SET "
               "status='RUNNING',finished_at=NULL,upload_targets=$2::jsonb "
               "WHERE id=$1::bigint",
               {std::to_string(current_id), grants});
      dv::ResultRequest failure;
      failure.set_agent_id(agent_id);
      failure.set_task_id(std::stoll(task_id));
      failure.set_attempt_id(current_id);
      failure.set_success(false);
      failure.set_error_code("RUNNER_FAILED");
      failure.set_exit_code(7);
      auto *diagnostic = failure.add_artifacts();
      diagnostic->set_kind("LOG");
      diagnostic->set_name("runner.log");
      diagnostic->set_format("log_jsonl");
      diagnostic->set_content_type("text/plain");
      diagnostic->set_object_key(log_key);
      diagnostic->set_size(123);
      diagnostic->set_sha256(std::string(64, 'a'));
      diagnostic->set_metadata_json("{\"fixture\":true,\"redacted\":true}");
      {
        grpc::ClientContext context;
        timeout(context);
        require(hot->NotifyResult(&context, failure, &response).ok(),
                "Failure LOG manifest rejected");
      }
      require(
          db->exec("SELECT collection_status FROM tasks WHERE id=$1::bigint",
                   {task_id})
                  .get(0, "collection_status") == "FAILED",
          "Failure LOG changed terminal task semantics");
      require(db->exec("SELECT count(*) AS count FROM artifacts WHERE "
                       "task_id=$1::bigint AND kind='LOG'",
                       {task_id})
                      .get(0, "count") == "1",
              "Failure LOG was not stored");
      require(
          db->exec("SELECT (jsonb_array_length(upload_targets)=1 AND NOT "
                   "(upload_targets->0 ? 'put_url') AND (SELECT count(*) FROM "
                   "jsonb_object_keys(upload_targets->0))=6) AS safe FROM "
                   "task_attempts WHERE id=$1::bigint",
                   {std::to_string(current_id)})
                  .get(0, "safe") == "t",
          "Terminal LOG grant retained a PUT credential");
      {
        grpc::ClientContext context;
        timeout(context);
        require(hot->NotifyResult(&context, failure, &response).ok() &&
                    response.error_code() == "ATTEMPT_TERMINAL",
                "Terminal LOG idempotent replay failed");
      }
      diagnostic->set_sha256(std::string(64, 'b'));
      {
        grpc::ClientContext context;
        timeout(context);
        require(hot->NotifyResult(&context, failure, &response).error_code() ==
                    grpc::StatusCode::INVALID_ARGUMENT,
                "Conflicting terminal LOG replay accepted");
      }
      diagnostic->set_sha256(std::string(64, 'a'));
      db->exec("DELETE FROM artifacts WHERE task_id=$1::bigint", {task_id});
      db->exec("UPDATE task_attempts SET finished_at=now()-interval '121 "
               "seconds' WHERE id=$1::bigint",
               {std::to_string(current_id)});
      {
        grpc::ClientContext context;
        timeout(context);
        require(hot->NotifyResult(&context, failure, &response).error_code() ==
                    grpc::StatusCode::INVALID_ARGUMENT,
                "Expired terminal LOG grace accepted");
      }
      db->exec("UPDATE task_attempts SET finished_at=now() WHERE id=$1::bigint",
               {std::to_string(current_id)});
      {
        grpc::ClientContext context;
        timeout(context);
        require(hot->NotifyResult(&context, failure, &response).ok(),
                "Current terminal LOG grace rejected");
      }
      failure.set_attempt_id(old_id);
      {
        grpc::ClientContext context;
        timeout(context);
        require(hot->NotifyResult(&context, failure, &response).error_code() ==
                    grpc::StatusCode::NOT_FOUND,
                "Stale attempt committed a LOG artifact");
      }
    }
    const auto secondary_address = secret("TEST_SECONDARY_SERVER_ADDRESS");
    if (!dispatch_only) {
      for (const bool modern : {false, true}) {
        db->exec(
            "UPDATE agents SET status='online',last_seen_at=now(),"
            "labels=$2::jsonb WHERE id=$1",
            {agent_id, modern ? "{\"collection_manifest_version\":1}" : "{}"});
        const auto queued = db->exec(
            "INSERT INTO tasks(name,kind,target_agent_id,creator_id,request_id,"
            "params,collection_status,collection_deadline) VALUES("
            "'Imported manifest negotiation fixture','PROC_CPU',$1,$2,$2,"
            "'{\"pid\":123,\"duration_seconds\":1,\"frequency_hz\":10,"
            "\"traceparent\":\"00-11111111111111111111111111111111-"
            "2222222222222222-01\"}',"
            "'QUEUED',now()+interval '120 seconds') RETURNING id",
            {agent_id, user_id});
        const auto queued_id = queued.get(0, "id");
        const auto grants = nlohmann::json::array(
            {{{"name", "collection-manifest.json"},
              {"kind", "MANIFEST"},
              {"object_key", "manifest/tasks/" + queued_id +
                                 "/attempts/1/collection-manifest.json"},
              {"put_url", "https://fixture.invalid/unused"},
              {"max_file_bytes", 65536},
              {"format", "collection_manifest_v1"},
              {"content_type", "application/json"}}});
        const auto attempt = db->exec(
            "INSERT INTO task_attempts(task_id,agent_id,status,raw_object_key,"
            "raw_upload_url,upload_targets) VALUES($1::bigint,$2,'QUEUED',$3,"
            "'https://fixture.invalid/unused',$4::jsonb) RETURNING id",
            {queued_id, agent_id,
             "raw/tasks/" + queued_id + "/attempts/1/profile.folded",
             grants.dump()});
        dv::CollectRequest request;
        request.set_agent_id(agent_id);
        dv::HeartbeatResponse reply;
        grpc::ClientContext context;
        timeout(context);
        require(hot->Collect(&context, request, &reply).ok() &&
                    reply.tasks_size() == 1 &&
                    reply.tasks(0).upload_targets_size() == (modern ? 1 : 0),
                "Manifest grant dispatch ignored Agent capability label");
        require(
            db->exec("SELECT jsonb_array_length(upload_targets) AS count FROM "
                     "task_attempts WHERE id=$1::bigint",
                     {attempt.get(0, "id")})
                    .get(0, "count") == (modern ? "1" : "0"),
            "N-1 Agent retained an unsatisfiable manifest requirement");
        dv::TaskReference cancel;
        cancel.set_task_id(std::stoll(queued_id));
        cancel.set_attempt_id(std::stoll(attempt.get(0, "id")));
        grpc::ClientContext cancel_context;
        timeout(cancel_context);
        require(control->CancelTask(&cancel_context, cancel, &response).ok(),
                "Manifest negotiation fixture cancellation failed");
      }
    }
    if (!secondary_address.empty()) {
      auto secondary = dv::Hotmethod::NewStub(channel(
          "TEST_AGENT_CERT_FILE", "TEST_AGENT_KEY_FILE", secondary_address));
      db->exec(
          "UPDATE agents SET status='online',last_seen_at=now() WHERE id=$1",
          {agent_id});
      {
        // Establish secondary mTLS before opening the race window; otherwise
        // handshake latency could accidentally serialize the negative control.
        dv::CollectRequest request;
        request.set_agent_id(agent_id);
        dv::HeartbeatResponse reply;
        grpc::ClientContext context;
        timeout(context);
        require(secondary->Collect(&context, request, &reply).ok() &&
                    reply.tasks_size() == 0,
                "Secondary Server fixture connection failed");
      }
      // Make the otherwise narrow race deterministic in this private DB. With
      // no Agent row lock, both Server replicas pass the active-task check,
      // then lock different QUEUED tasks and sleep before either can commit.
      db->exec("CREATE FUNCTION drop_rpc_fixture_delay() RETURNS trigger "
               "LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(0.4); RETURN "
               "NEW; END $$");
      db->exec("CREATE TRIGGER drop_rpc_fixture_delay BEFORE UPDATE ON tasks "
               "FOR EACH ROW WHEN (NEW.collection_status='DELIVERED') "
               "EXECUTE FUNCTION drop_rpc_fixture_delay()");
      for (int index = 0; index < 2; ++index) {
        const auto queued = db->exec(
            "INSERT INTO tasks(name,kind,target_agent_id,creator_id,request_id,"
            "params,collection_status,collection_deadline,priority) "
            "VALUES('Isolated concurrent dispatch fixture','PROC_CPU',$1,$2,"
            "$3,'{\"pid\":123,\"duration_seconds\":1,\"frequency_hz\":10,"
            "\"traceparent\":\"00-11111111111111111111111111111111-"
            "2222222222222222-01\"}',"
            "'CREATED',now()+interval '120 seconds',$4::smallint) RETURNING id",
            {agent_id, user_id, user_id + "-dispatch-" + std::to_string(index),
             index == 0 ? "-3" : "3"});
        const auto queued_attempt = db->exec(
            "INSERT INTO task_attempts(task_id,attempt,agent_id,status,"
            "raw_object_key,raw_upload_url) VALUES($1::bigint,1,$2,"
            "'QUEUED',$3,'http://private-fixture.invalid/unused') RETURNING id",
            {queued.get(0, "id"), agent_id,
             "fixture/" + user_id + "/" + std::to_string(index)});
        dv::TaskDesc descriptor;
        descriptor.set_task_id(std::stoll(queued.get(0, "id")));
        descriptor.set_attempt_id(std::stoll(queued_attempt.get(0, "id")));
        descriptor.set_target_agent_id(agent_id);
        // Proto default zero models an N-1 API that cannot express priority.
        grpc::ClientContext context;
        timeout(context);
        require(control->CreateTask(&context, descriptor, &response).ok(),
                "N-1 API default priority could not queue persisted nonzero "
                "priority");
      }
      std::barrier start(2);
      const auto collect = [&](dv::Hotmethod::Stub &stub) {
        start.arrive_and_wait();
        dv::CollectRequest request;
        request.set_agent_id(agent_id);
        dv::HeartbeatResponse reply;
        grpc::ClientContext context;
        timeout(context);
        const auto status = stub.Collect(&context, request, &reply);
        return std::pair{status, reply.tasks_size()};
      };
      auto first =
          std::async(std::launch::async, [&] { return collect(*hot); });
      auto second =
          std::async(std::launch::async, [&] { return collect(*secondary); });
      const auto first_result = first.get(), second_result = second.get();
      require(first_result.first.ok() && second_result.first.ok(),
              "Concurrent cross-Server Collect failed");
      require(first_result.second + second_result.second == 1,
              "Concurrent Server replicas dispatched more than one Agent task");
      if (!dispatch_only) {
        require(db->exec("SELECT priority FROM tasks WHERE creator_id=$1 AND "
                         "collection_status='DELIVERED'",
                         {user_id})
                        .get(0, "priority") == "3",
                "Persisted priority did not determine dispatch ordering");
        const auto remaining =
            db->exec("SELECT t.id,a.id AS attempt_id FROM tasks t JOIN "
                     "task_attempts a ON a.task_id=t.id WHERE t.creator_id=$1 "
                     "AND t.collection_status='QUEUED'",
                     {user_id});
        dv::TaskDesc mismatch;
        mismatch.set_task_id(std::stoll(remaining.get(0, "id")));
        mismatch.set_attempt_id(std::stoll(remaining.get(0, "attempt_id")));
        mismatch.set_target_agent_id(agent_id);
        mismatch.set_priority(4);
        grpc::ClientContext context;
        timeout(context);
        require(
            control->CreateTask(&context, mismatch, &response).error_code() ==
                grpc::StatusCode::INVALID_ARGUMENT,
            "Explicit mismatched RPC priority accepted");
      }
      require(
          db->exec("SELECT count(*) AS count FROM tasks WHERE creator_id=$1 "
                   "AND collection_status='DELIVERED'",
                   {user_id})
                  .get(0, "count") == "1",
          "Cross-Server Agent concurrency budget exceeded");
    }
    cleanup();
    std::cout << "rpc-probe: mTLS identity isolation, stale cancellation, "
                 "stale heartbeat lease, current cancellation and configured "
                 "cross-Server dispatch checks passed\n";
  } catch (const std::exception &e) {
    std::cerr << "rpc-probe failed: " << e.what() << '\n';
    try {
      cleanup();
    } catch (...) {
    }
    return 1;
  }
}
