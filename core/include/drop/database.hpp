// libpq薄封装：Rows负责结果生命周期，Database使用参数化SQL，Transaction提供RAII回滚。
// 调用方仍需按固定顺序持有数据库互斥锁及业务行锁；封装本身不自动解决多副本竞争。
// 只有显式commit后析构才不回滚，因此状态、制品和事件应放在同一业务事务内。

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
      // 连接丢失时，原事务已经失效；不能换一条新连接后继续执行剩余语句，
      // 否则原本应当原子提交的状态与事件会被拆开。
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
} // 结束 dropcore 命名空间
