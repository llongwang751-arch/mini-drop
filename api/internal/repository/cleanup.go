package repository

import "context"

func (r *Repository) CleanupEphemeral(ctx context.Context) error {
	for _, sql := range []string{
		"DELETE FROM rate_limits WHERE key IN (SELECT key FROM rate_limits WHERE expires_at<now() ORDER BY expires_at LIMIT 1000)",
		"DELETE FROM oidc_states WHERE state_hash IN (SELECT state_hash FROM oidc_states WHERE expires_at<now() ORDER BY expires_at LIMIT 1000)",
		"DELETE FROM sessions WHERE token_hash IN (SELECT token_hash FROM sessions WHERE expires_at<now() ORDER BY expires_at LIMIT 1000)",
	} {
		if err := r.Context(ctx).Exec(sql).Error; err != nil {
			return err
		}
	}
	return r.CleanupUploadGrants(ctx)
}
