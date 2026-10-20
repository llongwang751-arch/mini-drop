package service

import (
	"context"
	"crypto/subtle"
	"errors"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/coreos/go-oidc/v3/oidc"
	"github.com/google/uuid"
	"golang.org/x/oauth2"
	"gorm.io/gorm"

	"drop/api/internal/domain"
	"drop/api/internal/repository"
)

type OIDCConfig struct {
	Issuer, ClientID, ClientSecret, RedirectURL, Name string
	AllowHTTP                                         bool
}
type OIDC struct {
	Repo       *repository.Repository
	Config     OIDCConfig
	OAuth      oauth2.Config
	Verifier   *oidc.IDTokenVerifier
	HTTPClient *http.Client
}
type oidcTransport struct {
	base      http.RoundTripper
	allowHTTP bool
}

func (t oidcTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if req.URL.Scheme != "https" && !(t.allowHTTP && req.URL.Scheme == "http") {
		return nil, errors.New("insecure identity endpoint")
	}
	response, err := t.base.RoundTrip(req)
	if err != nil {
		return nil, err
	}
	response.Body = http.MaxBytesReader(nil, response.Body, 1<<20)
	return response, nil
}

func NewOIDC(ctx context.Context, r *repository.Repository, c OIDCConfig) (*OIDC, error) {
	for _, raw := range []string{c.Issuer, c.RedirectURL} {
		u, err := url.Parse(raw)
		if err != nil || u.Host == "" || u.User != nil || u.Fragment != "" || (u.Scheme != "https" && !(c.AllowHTTP && u.Scheme == "http")) {
			return nil, errors.New("OIDC issuer/redirect must be trusted HTTPS URLs")
		}
	}
	if c.ClientID == "" {
		return nil, errors.New("OIDC client ID required")
	}
	client := &http.Client{Timeout: 15 * time.Second, Transport: oidcTransport{base: http.DefaultTransport, allowHTTP: c.AllowHTTP}, CheckRedirect: func(req *http.Request, via []*http.Request) error {
		if len(via) >= 3 {
			return errors.New("too many identity redirects")
		}
		if req.URL.Scheme != "https" && !c.AllowHTTP {
			return errors.New("insecure identity redirect")
		}
		return nil
	}}
	ctx = oidc.ClientContext(ctx, client)
	provider, err := oidc.NewProvider(ctx, c.Issuer)
	if err != nil {
		return nil, errors.New("OIDC discovery failed")
	}
	for _, endpoint := range []string{provider.Endpoint().AuthURL, provider.Endpoint().TokenURL} {
		u, err := url.Parse(endpoint)
		if err != nil || u.Host == "" || u.User != nil || (u.Scheme != "https" && !(c.AllowHTTP && u.Scheme == "http")) {
			return nil, errors.New("OIDC discovery contained insecure endpoints")
		}
	}
	if c.Name == "" {
		c.Name = "企业账号"
	}
	return &OIDC{Repo: r, Config: c, OAuth: oauth2.Config{ClientID: c.ClientID, ClientSecret: c.ClientSecret, Endpoint: provider.Endpoint(), RedirectURL: c.RedirectURL, Scopes: []string{oidc.ScopeOpenID, "profile"}}, Verifier: provider.Verifier(&oidc.Config{ClientID: c.ClientID}), HTTPClient: client}, nil
}

type OIDCState struct {
	StateHash   string `gorm:"primaryKey"`
	BrowserHash string
	Nonce       string
	Verifier    string
	ExpiresAt   time.Time
	CreatedAt   time.Time
}

func (OIDCState) TableName() string { return "oidc_states" }
func (o *OIDC) Start(ctx context.Context) (string, string, error) {
	state, browser, nonce, verifier := RandomToken(), RandomToken(), RandomToken(), oauth2.GenerateVerifier()
	row := OIDCState{StateHash: repository.Digest(state), BrowserHash: repository.Digest(browser), Nonce: nonce, Verifier: verifier, ExpiresAt: time.Now().Add(10 * time.Minute), CreatedAt: time.Now().UTC()}
	if err := o.Repo.Context(ctx).Create(&row).Error; err != nil {
		return "", "", err
	}
	return o.OAuth.AuthCodeURL(state, oidc.Nonce(nonce), oauth2.S256ChallengeOption(verifier)), browser, nil
}
func (o *OIDC) Callback(ctx context.Context, state, browser, code, request string) (domain.User, string, string, error) {
	fail := func() (domain.User, string, string, error) {
		return domain.User{}, "", "", domain.Fail("INVALID_CREDENTIALS", "OIDC身份验证失败，请重新登录")
	}
	if len(state) != 64 || len(browser) != 64 || code == "" || len(code) > 8192 {
		return fail()
	}
	var row OIDCState
	err := o.Repo.Context(ctx).Raw("DELETE FROM oidc_states WHERE state_hash=? AND browser_hash=? AND expires_at>now() RETURNING *", repository.Digest(state), repository.Digest(browser)).Scan(&row).Error
	if err != nil {
		return domain.User{}, "", "", err
	}
	if row.StateHash == "" {
		return fail()
	}
	ctx = oidc.ClientContext(ctx, o.HTTPClient)
	token, err := o.OAuth.Exchange(ctx, code, oauth2.VerifierOption(row.Verifier))
	if err != nil {
		return fail()
	}
	raw, ok := token.Extra("id_token").(string)
	if !ok {
		return fail()
	}
	id, err := o.Verifier.Verify(ctx, raw)
	if err != nil {
		return fail()
	}
	if subtle.ConstantTimeCompare([]byte(id.Nonce), []byte(row.Nonce)) != 1 || id.Subject == "" || len(id.Subject) > 512 {
		return fail()
	}
	var claims struct {
		Name   string `json:"name"`
		AZP    string `json:"azp"`
		AtHash string `json:"at_hash"`
	}
	if err := id.Claims(&claims); err != nil {
		return fail()
	}
	if (len(id.Audience) > 1 || claims.AZP != "") && claims.AZP != o.Config.ClientID {
		return fail()
	}
	if claims.AtHash != "" && id.VerifyAccessToken(token.AccessToken) != nil {
		return fail()
	}
	var u domain.User
	err = o.Repo.Context(ctx).Transaction(func(tx *gorm.DB) error {
		if err := tx.Exec("SELECT pg_advisory_xact_lock(hashtextextended(?,0))", "oidc:"+o.Config.Issuer+":"+id.Subject).Error; err != nil {
			return err
		}
		var identity struct{ UserID string }
		if err := tx.Table("oidc_identities").Where("issuer=? AND subject=?", o.Config.Issuer, id.Subject).Find(&identity).Error; err != nil {
			return err
		}
		if identity.UserID != "" {
			return tx.Where("id=?", identity.UserID).First(&u).Error
		}
		name := strings.TrimSpace(claims.Name)
		if len(name) > 120 {
			name = "OIDC用户"
		}
		if name == "" {
			name = "OIDC用户"
		}
		u = domain.User{ID: uuid.NewString(), Username: "oidc_" + repository.Digest(o.Config.Issuer + "\x00" + id.Subject)[:24], DisplayName: name, Role: "viewer", CreatedAt: time.Now().UTC()}
		if err := tx.Create(&u).Error; err != nil {
			return err
		}
		return tx.Exec("INSERT INTO oidc_identities(issuer,subject,user_id) VALUES(?,?,?)", o.Config.Issuer, id.Subject, u.ID).Error
	})
	if err != nil {
		return domain.User{}, "", "", err
	}
	sessionToken, csrf := RandomToken(), RandomToken()
	if err := o.Repo.NewSession(ctx, repository.Session{TokenHash: repository.Digest(sessionToken), UserID: u.ID, CSRFToken: csrf, ExpiresAt: time.Now().Add(12 * time.Hour), CreatedAt: time.Now().UTC()}); err != nil {
		return u, "", "", err
	}
	if err := o.Repo.Audit(ctx, Audit(u, "auth.oidc_login", "user", u.ID, request)); err != nil {
		return u, "", "", err
	}
	return u, sessionToken, csrf, nil
}
