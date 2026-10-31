package main

import (
	"errors"
	"net/http"
	"testing"

	"github.com/google/go-containerregistry/pkg/v1/remote/transport"
)

func TestCategory(t *testing.T) {
	tests := []struct {
		err  error
		want string
	}{
		{&transport.Error{StatusCode: http.StatusForbidden}, "http-403"},
		{&transport.Error{StatusCode: http.StatusServiceUnavailable}, "http-5xx"},
		{errDigestMismatch, "digest-mismatch"},
		{errors.New("lookup registry.invalid: no such host"), "dns"},
		{errors.New("tls: x509: certificate signed by unknown authority"), "tls"},
		{errors.New("read: connection reset by peer"), "connection"},
		{errors.New("opaque registry failure with token=not-printed"), "unknown"},
	}
	for _, test := range tests {
		if got := category(test.err); got != test.want {
			t.Fatalf("category() = %q, want %q", got, test.want)
		}
	}
}

func TestFailureMessageDoesNotExposeRawError(t *testing.T) {
	message := failureMessage("destination-write", errors.New("token=secret https://registry.invalid/private"))
	if message != "registry copy failed or outcome uncertain (stage=destination-write; category=unknown)" {
		t.Fatalf("unexpected failure message %q", message)
	}
}
