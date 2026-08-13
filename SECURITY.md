# Security

## Trust boundary

Research agents process untrusted documents and web content. Cached pages can contain prompt injection, misleading instructions, malicious links, or sensitive material. Treat retrieved text as evidence to inspect, never as runtime instructions.

The default Codex profile restricts writes to the workspace while allowing outbound network access. It is not a complete read boundary. The default Claude Code profile enables filesystem and network sandboxing and fails closed when its sandbox is unavailable. These controls reduce host risk but do not make arbitrary web content trustworthy or prevent every form of data disclosure. Use a container or virtual machine without home-directory or credential mounts when host confidentiality matters; see [docs/least-privilege.md](docs/least-privilege.md).

## Credentials

- Keep keys in an untracked `.env` or the process environment.
- Use provider-scoped, revocable keys with the smallest practical quota.
- Never put credentials in manifests, prompts, logs, examples, issues, or pull requests.
- Run the credential scan in `scripts/release_check.py` before publishing.

## Unsafe mode

`batch.py --unsafe-unattended` invokes the runtimes' permission-bypass modes. Use it only in an externally isolated, disposable container or virtual machine with narrowly scoped credentials. The batch summary records this choice.

## Reporting a vulnerability

For a public GitHub repository, use a private security advisory. Until that channel exists, share a minimal reproduction privately with the maintainer. Do not include live credentials or personal research data.
