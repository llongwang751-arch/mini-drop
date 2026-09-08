# Mini-Drop workspace instructions

Before changing this repository, read these files in order:

1. `docs/PROJECT_CONTEXT.md`
2. `docs/RESTART_HANDOFF.md`
3. `docs/PROJECT_LEARNING_GUIDE.md`
4. The domain document linked from `docs/README.md` for the current task

Treat the documents above as the durable project context. Do not infer the
current architecture solely from the latest chat message.

Preserve user changes in the dirty worktree. Do not delete or replace the
canonical documents, deployment files, tests, benchmark evidence, database
volumes, or object-store data unless the user names the exact targets and
explicitly confirms that destructive scope.

When an architecture or deployment decision changes, update
`docs/PROJECT_CONTEXT.md` and the affected canonical document in the same
change. Generated files must be updated through their source contract or
generator rather than edited as an independent source of truth.
