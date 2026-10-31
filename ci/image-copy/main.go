package main

import (
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"strings"

	"github.com/google/go-containerregistry/pkg/authn"
	"github.com/google/go-containerregistry/pkg/name"
	"github.com/google/go-containerregistry/pkg/v1/remote"
	"github.com/google/go-containerregistry/pkg/v1/remote/transport"
)

func noRetry(error) bool { return false }

var errDigestMismatch = errors.New("digest mismatch")

func category(err error) string {
	if err == nil {
		return "unknown"
	}
	if errors.Is(err, errDigestMismatch) {
		return "digest-mismatch"
	}
	var registryError *transport.Error
	if errors.As(err, &registryError) {
		switch registryError.StatusCode {
		case http.StatusUnauthorized:
			return "http-401"
		case http.StatusForbidden:
			return "http-403"
		case http.StatusNotFound:
			return "http-404"
		case http.StatusConflict:
			return "http-409"
		case http.StatusTooManyRequests:
			return "http-429"
		default:
			if registryError.StatusCode >= 500 && registryError.StatusCode <= 599 {
				return "http-5xx"
			}
		}
	}
	var networkError net.Error
	if errors.As(err, &networkError) && networkError.Timeout() {
		return "timeout"
	}
	if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
		return "eof"
	}
	message := strings.ToLower(err.Error())
	switch {
	case strings.Contains(message, "no such host"), strings.Contains(message, "server misbehaving"):
		return "dns"
	case strings.Contains(message, "tls"), strings.Contains(message, "x509"):
		return "tls"
	case strings.Contains(message, "connection reset"), strings.Contains(message, "connection refused"):
		return "connection"
	case strings.Contains(message, "unexpected eof"):
		return "eof"
	default:
		return "unknown"
	}
}

func failureMessage(stage string, err error) string {
	return fmt.Sprintf("registry copy failed or outcome uncertain (stage=%s; category=%s)", stage, category(err))
}

func fail(stage string, err error) {
	fmt.Fprintln(os.Stderr, failureMessage(stage, err))
	os.Exit(1)
}

func main() {
	if len(os.Args) != 4 {
		fail("input", nil)
	}
	source, err := name.ParseReference(os.Args[1], name.StrictValidation)
	if err != nil {
		fail("input", err)
	}
	target, err := name.NewTag(os.Args[2], name.StrictValidation)
	if err != nil {
		fail("input", err)
	}
	descriptor, err := remote.Get(source, remote.WithAuthFromKeychain(authn.DefaultKeychain))
	if err != nil {
		fail("source-descriptor", err)
	}
	if descriptor.Digest.String() != os.Args[3] {
		fail("source-digest", errDigestMismatch)
	}
	opts := []remote.Option{remote.WithAuthFromKeychain(authn.DefaultKeychain), remote.WithRetryPredicate(noRetry), remote.WithRetryStatusCodes()}
	if descriptor.MediaType.IsIndex() {
		index, err := descriptor.ImageIndex()
		if err != nil {
			fail("source-image", err)
		}
		if err = remote.WriteIndex(target, index, opts...); err != nil {
			fail("destination-write", err)
		}
	} else {
		image, err := descriptor.Image()
		if err != nil {
			fail("source-image", err)
		}
		if err = remote.Write(target, image, opts...); err != nil {
			fail("destination-write", err)
		}
	}
	fmt.Println(os.Args[3])
}
