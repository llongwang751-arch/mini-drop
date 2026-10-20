package repository

import (
	"testing"

	"drop/api/internal/domain"
)

func TestOldCancellationCannotCancelRetriedAttempt(t *testing.T) {
	o := domain.Outbox{AttemptID: 1, EventType: "CANCEL"}
	a := domain.Attempt{ID: 1, Status: "CANCELED"}
	if Dispatchable(o, domain.Task{CollectionStatus: "CREATED"}, a, 2) {
		t.Fatal("old cancellation accepted for new attempt")
	}
	if Dispatchable(o, domain.Task{CollectionStatus: "CANCELED"}, a, 2) {
		t.Fatal("old cancellation accepted even though current attempt changed")
	}
	if !Dispatchable(o, domain.Task{CollectionStatus: "CANCELED"}, a, 1) {
		t.Fatal("current cancellation rejected")
	}
}
func TestCanceledTaskCannotBeDispatched(t *testing.T) {
	o := domain.Outbox{AttemptID: 1, EventType: "DISPATCH"}
	a := domain.Attempt{ID: 1, Status: "CREATED"}
	if Dispatchable(o, domain.Task{CollectionStatus: "CANCELED"}, a, 1) {
		t.Fatal("canceled task dispatched")
	}
}
