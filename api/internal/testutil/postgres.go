package testutil

import (
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"testing"

	"drop/api/internal/domain"
	"github.com/google/uuid"
	"gorm.io/driver/postgres"
	"gorm.io/gorm"
	"gorm.io/gorm/logger"
)

func Postgres(t *testing.T) *gorm.DB {
	t.Helper()
	dsn := os.Getenv("TEST_DATABASE_URL")
	if file := os.Getenv("TEST_DATABASE_URL_FILE"); file != "" {
		b, err := os.ReadFile(file)
		if err != nil {
			t.Fatal(err)
		}
		dsn = strings.TrimSpace(string(b))
	}
	if dsn == "" {
		t.Skip("TEST_DATABASE_URL required for real PostgreSQL integration")
	}
	open := func(s string) *gorm.DB {
		db, err := gorm.Open(postgres.New(postgres.Config{DSN: s, PreferSimpleProtocol: true}), &gorm.Config{Logger: logger.Default.LogMode(logger.Silent)})
		if err != nil {
			t.Fatal("integration database connection failed")
		}
		return db
	}
	base := open(dsn)
	baseSQL, _ := base.DB()
	schema := "drop_test_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	if err := base.Exec("CREATE SCHEMA " + schema).Error; err != nil {
		t.Fatal(err)
	}
	u, err := url.Parse(dsn)
	if err != nil {
		t.Fatal(err)
	}
	q := u.Query()
	q.Set("search_path", schema)
	u.RawQuery = q.Encode()
	db := open(u.String())
	sqlDB, _ := db.DB()
	sqlDB.SetMaxOpenConns(16)
	t.Cleanup(func() { sqlDB.Close(); base.Exec("DROP SCHEMA " + schema + " CASCADE"); baseSQL.Close() })
	dir := os.Getenv("TEST_MIGRATIONS_DIR")
	if dir == "" {
		dir = filepath.Join("..", "..", "..", "db", "migrations")
	}
	files, err := filepath.Glob(filepath.Join(dir, "*.sql"))
	if err != nil || len(files) < 2 {
		t.Fatal("all migration files required")
	}
	sort.Strings(files)
	for _, f := range files {
		b, err := os.ReadFile(f)
		if err != nil {
			t.Fatal(err)
		}
		if err := db.Exec(string(b)).Error; err != nil {
			t.Fatalf("migration %s: %v", filepath.Base(f), err)
		}
	}
	kinds := os.Getenv("TEST_TASK_KINDS_FILE")
	if kinds == "" {
		kinds = filepath.Join("..", "..", "..", "contracts", "task-kinds.json")
	}
	b, err := os.ReadFile(kinds)
	if err != nil {
		t.Fatal(err)
	}
	if err := domain.LoadKinds(b); err != nil {
		t.Fatal(err)
	}
	return db
}
