package repository

import "drop/api/internal/domain"

// A delayed outbox entry may only affect the attempt which created it.
func Dispatchable(o domain.Outbox, t domain.Task, a domain.Attempt, latestID int64) bool {
	if o.AttemptID != latestID || a.ID != latestID {
		return false
	}
	if o.EventType == "CANCEL" {
		return t.CollectionStatus == "CANCELED" && a.Status == "CANCELED"
	}
	return o.EventType == "DISPATCH" && t.CollectionStatus == "CREATED" && a.Status == "CREATED"
}
