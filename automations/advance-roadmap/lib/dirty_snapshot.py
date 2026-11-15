#!/usr/bin/env python3
"""Hash a dirty checkout using Slackagent's tracked and untracked snapshot format."""
import argparse
import hashlib
import json
import subprocess


def fingerprint(workspace):
    def git(*args):
        result = subprocess.run(['git', '-C', str(workspace), *args], check=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return result.stdout.decode('utf-8', errors='replace')
    unstaged = git('diff', '--no-ext-diff', '--binary')
    staged = git('diff', '--cached', '--no-ext-diff', '--binary')
    names = sorted(filter(None, git('ls-files', '--others', '--exclude-standard', '-z').split('\0')),
                   key=lambda value: value.encode('utf-16-be', errors='surrogatepass'))
    files = [[name, git('hash-object', '--', name).strip()] for name in names]
    encoded = json.dumps([unstaged, staged, files], ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workspace')
    args = parser.parse_args()
    print(fingerprint(args.workspace))


if __name__ == '__main__':
    main()
