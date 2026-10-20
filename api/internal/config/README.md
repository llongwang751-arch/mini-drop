# API configuration

The API accepts `drop-api [migrate] --config /etc/drop/api.json --set HTTP_LISTEN=:8080`.
Repeated `--set KEY=VALUE` options override environment values, which override
JSON settings, which override safe defaults. The optional `migrate` command must
come first. Unknown options, duplicate logical CLI settings, duplicate JSON keys,
non-string JSON values, control characters and files larger than 64 KiB are
rejected without echoing their contents. JSON and Secret file descriptors must be
regular files. Symlinks used by Secret projection volumes remain supported;
Linux opens without blocking so a FIFO target cannot stall before validation.

Only keys registered in `config.go` are accepted. Existing environment `NAME_FILE`
references retain precedence over `NAME` within the same layer. Secret contents
may come from environment variables or file references; JSON and CLI must use
`DATABASE_URL_FILE`, `S3_ACCESS_KEY_FILE`, `S3_SECRET_KEY_FILE`,
`ADMIN_USERNAME_FILE`, `ADMIN_PASSWORD_FILE`, `OIDC_CLIENT_SECRET_FILE` or
`AI_API_KEY_FILE` instead of inline values. Existing `TLS_*_FILE` settings are
certificate/key path references and remain unchanged.

Startup emits a safe effective projection and its SHA256. Credentials, file
paths, URL userinfo, query strings, fragments and endpoint paths are omitted;
credential rotation therefore does not change this hash. It is a public
configuration comparison aid, not a digest of credentials or deployment source.
The ordinary settings and safe endpoint origin/address still participate in the
hash. Default OIDC callback derivation uses the resolved public URL.
