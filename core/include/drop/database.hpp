#pragma once

#include <libpq-fe.h>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <vector>

namespace dropcore {
class Rows {
public:
  explicit Rows(PGresult *result) : result_(result, PQclear) {
    const auto status = PQresultStatus(result);
    if (status != PGRES_COMMAND_OK && status != PGRES_TUPLES_OK)
      throw std::runtime_error("Database operation failed");
  }
  int count() const { return PQntuples(result_.get()); }
  std::string get(int row, const char *field) const {
    int column = PQfnumber(result_.get(), field);
    if (column < 0)
      throw std::runtime_error("Missing database column");
    if (PQgetisnull(result_.get(), row, column))
      return "";
    return PQgetvalue(result_.get(), row, column);
  }

private:
  std::unique_ptr<PGresult, decltype(&PQclear)> result_;
};

class Database {
public:
  explicit Database(const std::string &dsn)
      : connection_(PQconnectdb(dsn.c_str()), PQfinish) {
    if (PQstatus(connection_.get()) != CONNECTION_OK)
      throw std::runtime_error("Database unavailable");
    exec("SET statement_timeout='10s'");
    exec("SET lock_timeout='3s'");
  }
  Rows exec(const std::string &sql,
            const std::vector<std::string> &parameters = {}) {
    if (PQstatus(connection_.get()) != CONNECTION_OK) {
      PQreset(connection_.get());
      if (PQstatus(connection_.get()) != CONNECTION_OK)
        throw std::runtime_error("Database unavailable");
      // A lost connection invalidates its transaction. Do not silently continue
      // an in-flight transaction on a fresh connection.
      throw std::runtime_error("Database reconnected; retry the operation");
    }
    std::vector<const char *> values;
    for (const auto &parameter : parameters)
      values.push_back(parameter.c_str());
    return Rows(PQexecParams(connection_.get(), sql.c_str(),
                             static_cast<int>(values.size()), nullptr,
                             values.data(), nullptr, nullptr, 0));
  }
  std::mutex mutex;

private:
  std::unique_ptr<PGconn, decltype(&PQfinish)> connection_;
};

class Transaction {
public:
  explicit Transaction(Database &db) : db_(db) { db_.exec("BEGIN"); }
  ~Transaction() {
    if (!committed_) {
      try {
        db_.exec("ROLLBACK");
      } catch (...) {
      }
    }
  }
  void commit() {
    db_.exec("COMMIT");
    committed_ = true;
  }

private:
  Database &db_;
  bool committed_ = false;
};
} // namespace dropcore
