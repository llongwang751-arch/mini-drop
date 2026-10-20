package control

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"fmt"
	"os"
	"time"

	"drop/api/internal/telemetry"
	"go.opentelemetry.io/contrib/instrumentation/google.golang.org/grpc/otelgrpc"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"

	pb "drop/api/gen/dropv1"
)

type Client struct {
	conn *grpc.ClientConn
	RPC  pb.ControlClient
}

func New(address, ca, cert, key string, metrics ...*telemetry.Metrics) (*Client, error) {
	pem, err := os.ReadFile(ca)
	if err != nil {
		return nil, err
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(pem) {
		return nil, fmt.Errorf("invalid grpc CA")
	}
	pair, err := tls.LoadX509KeyPair(cert, key)
	if err != nil {
		return nil, err
	}
	opts := []grpc.DialOption{grpc.WithTransportCredentials(credentials.NewTLS(&tls.Config{MinVersion: tls.VersionTLS12, RootCAs: pool, Certificates: []tls.Certificate{pair}, ServerName: "drop-server"})), grpc.WithDefaultCallOptions(grpc.MaxCallRecvMsgSize(1<<20), grpc.MaxCallSendMsgSize(1<<20)), grpc.WithStatsHandler(otelgrpc.NewClientHandler())}
	if len(metrics) > 0 && metrics[0] != nil {
		opts = append(opts, grpc.WithUnaryInterceptor(metrics[0].UnaryInterceptor))
	}
	conn, err := grpc.NewClient(address, opts...)
	if err != nil {
		return nil, err
	}
	return &Client{conn: conn, RPC: pb.NewControlClient(conn)}, nil
}
func (c *Client) Close() error { return c.conn.Close() }
func (c *Client) Ready(ctx context.Context) error {
	ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	ack, err := c.RPC.Ping(ctx, &pb.Empty{})
	if err != nil {
		return err
	}
	if !ack.Ok {
		return fmt.Errorf("core not ready")
	}
	return nil
}
func (c *Client) Dispatch(ctx context.Context, t *pb.TaskDesc) error {
	ack, err := c.RPC.CreateTask(ctx, t)
	if err != nil {
		return err
	}
	if !ack.Ok {
		return fmt.Errorf("%s", ack.ErrorCode)
	}
	return nil
}
func (c *Client) Cancel(ctx context.Context, id, attemptID int64, request string) error {
	ack, err := c.RPC.CancelTask(ctx, &pb.TaskReference{TaskId: id, AttemptId: attemptID, RequestId: request})
	if err != nil {
		return err
	}
	if !ack.Ok {
		return fmt.Errorf("%s", ack.ErrorCode)
	}
	return nil
}
func (c *Client) FetchData(ctx context.Context, id, category string, limit int) (json.RawMessage, error) {
	r, err := c.RPC.FetchData(ctx, &pb.DataRequest{AgentId: id, Category: category, Limit: int32(limit)})
	if err != nil {
		return nil, err
	}
	if !json.Valid([]byte(r.DataJson)) {
		return nil, fmt.Errorf("invalid agent diagnostics")
	}
	return json.RawMessage(r.DataJson), nil
}
