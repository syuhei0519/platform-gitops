package main

import (
	"fmt"
	"os"

	"github.com/google/go-containerregistry/pkg/authn"
	"github.com/google/go-containerregistry/pkg/name"
	"github.com/google/go-containerregistry/pkg/v1/remote"
)

func noRetry(error) bool { return false }
func fail() {
	fmt.Fprintln(os.Stderr, "registry write failed or outcome uncertain")
	os.Exit(1)
}

func main() {
	if len(os.Args) != 4 {
		fail()
	}
	source, err := name.ParseReference(os.Args[1], name.StrictValidation)
	if err != nil {
		fail()
	}
	target, err := name.NewTag(os.Args[2], name.StrictValidation)
	if err != nil {
		fail()
	}
	descriptor, err := remote.Get(source, remote.WithAuthFromKeychain(authn.DefaultKeychain))
	if err != nil || descriptor.Digest.String() != os.Args[3] {
		fail()
	}
	opts := []remote.Option{remote.WithAuthFromKeychain(authn.DefaultKeychain), remote.WithRetryPredicate(noRetry), remote.WithRetryStatusCodes()}
	if descriptor.MediaType.IsIndex() {
		index, err := descriptor.ImageIndex()
		if err != nil {
			fail()
		}
		if err = remote.WriteIndex(target, index, opts...); err != nil {
			fail()
		}
	} else {
		image, err := descriptor.Image()
		if err != nil {
			fail()
		}
		if err = remote.Write(target, image, opts...); err != nil {
			fail()
		}
	}
	fmt.Println(os.Args[3])
}
