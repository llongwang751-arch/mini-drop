#include "drop.grpc.pb.h"
#include "drop/database.hpp"
#include "drop/process.hpp"

#include <array>
#include <barrier>
#include <chrono>
#include <future>
#include <grpcpp/grpcpp.h>
#include <iostream>
#include <memory>
#include <nlohmann/json.hpp>
#include <stdexcept>
#include <string>
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
std::shared_ptr<grpc::Channel> channel(const std::string &address) {
  grpc::SslCredentialsOptions tls;
  tls.pem_root_certs = read_file(secret("TLS_CA_FILE"));
  tls.pem_cert_chain = read_file(secret("TEST_API_CERT_FILE"));
  tls.pem_private_key = read_file(secret("TEST_API_KEY_FILE"));
  grpc::ChannelArguments options;
  options.SetSslTargetNameOverride("drop-server");
  return grpc::CreateCustomChannel(address, grpc::SslCredentials(tls), options);
}
} // namespace

// Only run against the private empty database created by rpc_isolated.sh.
// This probe is intentionally excluded from offline CTest and normal startup.
int main() {
  const auto owner = "enqueue-probe-" + std::to_string(getpid());
  const auto agent = secret("AGENT_ID", "aliyun-local");
  std::unique_ptr<Database> db;
  bool owned_fixture = false, created_agent = false, created_trigger = false,
       created_function = false;
  auto cleanup = [&] {
    if (!db || !owned_fixture)
      return;
    if (created_trigger)
      db->exec("DROP TRIGGER IF EXISTS drop_enqueue_fixture_delay ON tasks");
    if (created_function)
      db->exec("DROP FUNCTION IF EXISTS drop_enqueue_fixture_delay()");
    Transaction transaction(*db);
    for (const auto *table : {"analysis_jobs", "artifacts", "task_events",
                              "outbox", "task_attempts"})
      db->exec(
          std::string("DELETE FROM ") + table +
              " WHERE task_id IN (SELECT id FROM tasks WHERE creator_id=$1)",
          {owner});
    db->exec("DELETE FROM tasks WHERE creator_id=$1", {owner});
    db->exec("DELETE FROM users WHERE id=$1", {owner});
    if (created_agent) {
      db->exec("DELETE FROM agent_events WHERE agent_id=$1", {agent});
      db->exec("DELETE FROM agent_samples WHERE agent_id=$1", {agent});
      db->exec("DELETE FROM agents WHERE id=$1", {agent});
    }
    transaction.commit();
  };
  try {
    require(secret("ALLOW_ISOLATED_RPC_FIXTURE") == "true",
            "Enqueue probe requires ALLOW_ISOLATED_RPC_FIXTURE=true");
    const auto primary = secret("DROP_SERVER_ADDRESS", "drop-server:50051");
    const auto secondary = secret("TEST_SECONDARY_SERVER_ADDRESS");
    require(!secondary.empty() && secondary != primary,
            "Two independent Server addresses are required");
    db = std::make_unique<Database>(secret("DATABASE_URL"));
    auto empty = db->exec("SELECT (SELECT count(*) FROM tasks) AS tasks,"
                          "(SELECT count(*) FROM agents) AS agents,"
                          "(SELECT count(*) FROM users) AS users");
    require(empty.get(0, "tasks") == "0" && empty.get(0, "agents") == "0" &&
                empty.get(0, "users") == "0",
            "Enqueue regression refuses a nonempty fixture database");
    auto first_channel = channel(primary), second_channel = channel(secondary);
    auto first = dv::Control::NewStub(first_channel);
    auto second = dv::Control::NewStub(second_channel);
    for (const auto &connection : {first_channel, second_channel})
      require(connection->WaitForConnected(std::chrono::system_clock::now() +
                                           std::chrono::seconds(5)),
              "Isolated Server mTLS connection failed");
    for (auto *control : {first.get(), second.get()}) {
      grpc::ClientContext context;
      timeout(context);
      dv::Empty request;
      dv::Ack response;
      require(control->Ping(&context, request, &response).ok() && response.ok(),
              "API mTLS channel warmup failed");
    }
    std::array<dv::TaskDesc, 2> candidates;
    {
      Transaction transaction(*db);
      owned_fixture = true;
      db->exec(
          "INSERT INTO users(id,username,display_name,role) "
          "VALUES($1,$1,'Imported enqueue concurrency fixture','operator')",
          {owner});
      db->exec("INSERT INTO agents(id,hostname,version,platform,capabilities) "
               "VALUES($1,'isolated-enqueue-fixture','fixture','linux',"
               "'[\"PROC_CPU\"]')",
               {agent});
      created_agent = true;
      for (int index = 0; index < 33; ++index) {
        const auto state = index < 31 ? "QUEUED" : "CREATED";
        auto task = db->exec(
            "INSERT INTO "
            "tasks(name,kind,target_agent_id,params,collection_status,"
            "creator_id,request_id,idempotency_key,collection_deadline) VALUES("
            "'Imported enqueue concurrency fixture','PROC_CPU',$1,"
            "'{\"pid\":123,\"duration_seconds\":1,\"frequency_hz\":10}',"
            "$2,$3,'enqueue-fixture',$4,now()+interval '120 seconds') "
            "RETURNING id",
            {agent, state, owner, owner + "-" + std::to_string(index)});
        const auto task_id = task.get(0, "id");
        const auto key = "raw/tasks/" + task_id + "/attempts/1/profile.folded";
        auto attempt = db->exec(
            "INSERT INTO task_attempts(task_id,agent_id,status,raw_object_key,"
            "raw_upload_url) VALUES($1::bigint,$2,$3,$4,"
            "'https://fixture.invalid/task-scoped-put') RETURNING id",
            {task_id, agent, state, key});
        if (index >= 31) {
          auto &descriptor = candidates[static_cast<std::size_t>(index - 31)];
          descriptor.set_task_id(std::stoll(task_id));
          descriptor.set_attempt_id(std::stoll(attempt.get(0, "id")));
          descriptor.set_target_agent_id(agent);
          descriptor.set_kind("PROC_CPU");
          descriptor.set_raw_object_key(key);
          descriptor.set_raw_upload_url(
              "https://fixture.invalid/task-scoped-put");
          descriptor.set_request_id("enqueue-fixture");
          descriptor.mutable_parameters()->set_pid(123);
          descriptor.mutable_parameters()->set_duration_seconds(1);
          descriptor.mutable_parameters()->set_frequency_hz(10);
        }
      }
      transaction.commit();
    }
    // Slow only the candidate's state update after the queue count. Without
    // shared serialization both replicas see 31 and commit a 33rd entry.
    db->exec(R"SQL(CREATE FUNCTION drop_enqueue_fixture_delay() RETURNS trigger
LANGUAGE plpgsql AS $fixture$
BEGIN
  IF OLD.collection_status='CREATED' AND NEW.collection_status='QUEUED' THEN
    PERFORM pg_sleep(0.4);
  END IF;
  RETURN NEW;
END;
$fixture$)SQL");
    created_function = true;
    db->exec("CREATE TRIGGER drop_enqueue_fixture_delay BEFORE UPDATE ON tasks "
             "FOR EACH ROW EXECUTE FUNCTION drop_enqueue_fixture_delay()");
    created_trigger = true;
    std::barrier start(3);
    const auto enqueue = [&](dv::Control::Stub &stub,
                             const dv::TaskDesc &task) {
      start.arrive_and_wait();
      grpc::ClientContext context;
      timeout(context);
      dv::Ack response;
      auto status = stub.CreateTask(&context, task, &response);
      return std::pair{status, response.ok()};
    };
    auto left = std::async(std::launch::async,
                           [&] { return enqueue(*first, candidates[0]); });
    auto right = std::async(std::launch::async,
                            [&] { return enqueue(*second, candidates[1]); });
    start.arrive_and_wait();
    const auto first_result = left.get(), second_result = right.get();
    int succeeded = 0, exhausted = 0;
    for (const auto &result : {first_result, second_result}) {
      succeeded += result.first.ok() && result.second ? 1 : 0;
      exhausted +=
          result.first.error_code() == grpc::StatusCode::RESOURCE_EXHAUSTED ? 1
                                                                            : 0;
    }
    const auto queue = db->exec(
        "SELECT count(*) FILTER(WHERE collection_status='QUEUED') AS queued,"
        "count(*) FILTER(WHERE collection_status='CREATED') AS created "
        "FROM tasks WHERE target_agent_id=$1 AND creator_id=$2",
        {agent, owner});
    std::cout
        << nlohmann::json{{"fixture", "isolated-two-server-enqueue"},
                          {"initial_queued", 31},
                          {"concurrent_requests", 2},
                          {"acknowledged", succeeded},
                          {"resource_exhausted", exhausted},
                          {"final_queued", std::stoi(queue.get(0, "queued"))},
                          {"final_created", std::stoi(queue.get(0, "created"))},
                          {"production_mutations", false}}
               .dump()
        << '\n';
    require(succeeded == 1 && exhausted == 1,
            "QUEUE_LIMIT_RACE: expected one ACK and one RESOURCE_EXHAUSTED");
    require(queue.get(0, "queued") == "32" && queue.get(0, "created") == "1",
            "QUEUE_LIMIT_RACE: shared Agent queue exceeded 32 entries");
    const auto events = db->exec(
        "SELECT count(*) AS count FROM task_events e JOIN tasks t ON "
        "t.id=e.task_id WHERE t.creator_id=$1 AND e.type='DISPATCH_QUEUED'",
        {owner});
    require(
        events.get(0, "count") == "1",
        "Rejected enqueue committed an event or successful enqueue lost it");
    cleanup();
    std::cout << "enqueue-probe: two Server replicas preserved the shared "
                 "queue limit\n";
    return 0;
  } catch (const std::exception &error) {
    try {
      cleanup();
    } catch (...) {
    }
    std::cerr << "enqueue-probe failed: " << error.what() << '\n';
    return 1;
  }
}
