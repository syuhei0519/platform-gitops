package main

import (
	"bytes"
	"errors"
	"io"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"strings"

	"gopkg.in/yaml.v3"
)

var sensitiveEnv = regexp.MustCompile(`(?i)(PASSWORD|TOKEN|SECRET|LICENSE[-_]KEY|API[-_]KEY|ACCESS[-_]KEY|PRIVATE[-_]KEY|AUTHORIZATION|CREDENTIAL)`)

func nodeFields(n *yaml.Node) map[string]*yaml.Node {
	m := map[string]*yaml.Node{}
	if n != nil && n.Kind == yaml.MappingNode {
		for i := 0; i < len(n.Content); i += 2 {
			m[n.Content[i].Value] = n.Content[i+1]
		}
	}
	return m
}

// A *_FILE value is a pointer only when this same container mounts an actual
// Secret volume read-only at its canonical absolute path. A suffix alone never
// exempts an environment value from the plaintext gate.
func secretFileReferences(root *yaml.Node) map[*yaml.Node]bool {
	allowed := map[*yaml.Node]bool{}
	var visit func(*yaml.Node, int)
	visit = func(n *yaml.Node, depth int) {
		if depth > 64 || n.Kind == yaml.AliasNode {
			return
		}
		m := nodeFields(n)
		volumes := map[string]bool{}
		if v := m["volumes"]; v != nil && v.Kind == yaml.SequenceNode {
			for _, volume := range v.Content {
				f := nodeFields(volume)
				secret := nodeFields(f["secret"])
				if name, source := f["name"], secret["secretName"]; name != nil && source != nil && source.Value != "" {
					volumes[name.Value] = true
				}
			}
		}
		for _, kind := range []string{"containers", "initContainers"} {
			if containers := m[kind]; containers != nil && containers.Kind == yaml.SequenceNode {
				for _, container := range containers.Content {
					c := nodeFields(container)
					mounts := c["volumeMounts"]
					env := c["env"]
					if mounts == nil || env == nil || mounts.Kind != yaml.SequenceNode || env.Kind != yaml.SequenceNode {
						continue
					}
					for _, entry := range env.Content {
						e := nodeFields(entry)
						name, value := e["name"], e["value"]
						if name == nil || value == nil || !sensitiveEnv.MatchString(name.Value) || !strings.HasSuffix(strings.ToUpper(name.Value), "_FILE") || !strings.HasPrefix(value.Value, "/") || path.Clean(value.Value) != value.Value || strings.Contains(value.Value, ":") {
							continue
						}
						for _, mount := range mounts.Content {
							f := nodeFields(mount)
							vname, mp, ro := f["name"], f["mountPath"], f["readOnly"]
							if vname == nil || mp == nil || ro == nil || ro.Tag != "!!bool" || ro.Value != "true" || !volumes[vname.Value] || !strings.HasPrefix(mp.Value, "/") || mp.Value == "/" || path.Clean(mp.Value) != mp.Value || f["subPath"] != nil || f["subPathExpr"] != nil {
								continue
							}
							if strings.HasPrefix(value.Value, mp.Value+"/") {
								allowed[entry] = true
							}
						}
					}
				}
			}
		}
		for _, child := range n.Content {
			visit(child, depth+1)
		}
	}
	visit(root, 0)
	return allowed
}

// Only fixed rule IDs and repository-relative paths leave this parser.
func inspectYAML(data []byte, file string) ([]Finding, error) {
	if len(data) > 32<<20 || !safePath(file) {
		return nil, errors.New("render input rejected")
	}
	d := yaml.NewDecoder(bytes.NewReader(data))
	findings := []Finding{}
	allowedFileEnv := map[*yaml.Node]bool{}
	var walk func(*yaml.Node, int) error
	walk = func(n *yaml.Node, depth int) error {
		if depth > 64 || n.Kind == yaml.AliasNode {
			return errors.New("render structure rejected")
		}
		if n.Kind == yaml.MappingNode {
			m := map[string]*yaml.Node{}
			for i := 0; i < len(n.Content); i += 2 {
				key := n.Content[i].Value
				if _, ok := m[key]; ok {
					return errors.New("duplicate render key")
				}
				m[key] = n.Content[i+1]
			}
			add := func(rule string) {
				findings = append(findings, Finding{Kind: "misconfiguration", Rule: rule, Path: file, Severity: "HIGH", Decision: "fail"})
			}
			if kind := m["kind"]; kind != nil && kind.Value == "Secret" {
				for _, key := range []string{"data", "stringData"} {
					if v := m[key]; v != nil && len(v.Content) > 0 {
						add("LOCAL-PLAINTEXT-SECRET")
						break
					}
				}
			}
			if v := m["privileged"]; v != nil && v.Tag == "!!bool" && v.Value == "true" {
				add("LOCAL-PRIVILEGED")
			}
			if name := m["name"]; name != nil && sensitiveEnv.MatchString(name.Value) {
				if value := m["value"]; value != nil && value.Value != "" && !allowedFileEnv[n] {
					add("LOCAL-PLAINTEXT-SECRET")
				}
			}
			if value := m["value"]; value != nil && value.Tag == "!!str" {
				if u, e := url.Parse(value.Value); e == nil && u.User != nil {
					add("LOCAL-PLAINTEXT-SECRET")
				}
			}
			if kind := m["kind"]; kind != nil && kind.Value == "ConfigMap" {
				if data := m["data"]; data != nil && data.Kind == yaml.MappingNode {
					for i := 0; i < len(data.Content); i += 2 {
						key, value := data.Content[i].Value, data.Content[i+1]
						if sensitiveEnv.MatchString(key) && value.Value != "" {
							add("LOCAL-PLAINTEXT-SECRET")
						}
					}
				}
			}
		}
		for _, c := range n.Content {
			if err := walk(c, depth+1); err != nil {
				return err
			}
		}
		return nil
	}
	for {
		var n yaml.Node
		err := d.Decode(&n)
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, errors.New("render YAML parse failed")
		}
		allowedFileEnv = secretFileReferences(&n)
		if err = walk(&n, 0); err != nil {
			return nil, err
		}
	}
	return findings, nil
}

func inspectRendered(dir string) ([]Finding, error) {
	findings := []Finding{}
	files := 0
	err := filepath.WalkDir(dir, func(name string, entry os.DirEntry, err error) error {
		if err != nil {
			return errors.New("rendered input unavailable")
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return errors.New("render symlink rejected")
		}
		if entry.IsDir() {
			return nil
		}
		ext := strings.ToLower(filepath.Ext(name))
		if ext != ".yaml" && ext != ".yml" && ext != ".json" {
			return nil
		}
		rel, e := filepath.Rel(dir, name)
		if e != nil {
			return errors.New("render path rejected")
		}
		b, e := os.ReadFile(name)
		if e != nil {
			return errors.New("render input read failed")
		}
		f, e := inspectYAML(b, "rendered/"+filepath.ToSlash(rel))
		if e != nil {
			return e
		}
		findings = append(findings, f...)
		files++
		return nil
	})
	if err != nil {
		return nil, err
	}
	if files == 0 {
		return nil, errors.New("rendered inputs missing")
	}
	return findings, nil
}
