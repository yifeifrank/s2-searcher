# Least-privilege operation

The research session needs a narrow set of capabilities: read task inputs, write
task artifacts, execute the bundled Python tools and `rg`, and contact selected
research providers. It does not need child agents, unrestricted host writes,
shell access to unrelated projects, SSH credentials, a Docker socket, or cloud
instance credentials.

## Included default

| Capability | Codex | Claude Code |
|---|---|---|
| Write generated workspace | `workspace-write` | sandbox allow-list |
| Write elsewhere | denied by sandbox | denied by sandbox |
| Network | enabled | research domains allow-listed |
| Interactive escalation | disabled (`never`) | disabled (`dontAsk`) |
| Child agents | prohibited by workflow | denied in project settings |
| Permission bypass | explicit flag only | explicit flag only |

This is a practical unattended default, not a complete confidentiality boundary.
In particular, Codex `workspace-write` primarily constrains writes; do not assume
that unrelated readable host files are hidden from the process. Networked agents
can also disclose data they are able to read.

## Stronger deployment for sensitive hosts

Run the generated workspace in a disposable container or virtual machine and
provide only:

1. the generated workspace as the working directory;
2. revocable, provider-scoped API credentials through environment variables;
3. an egress allow-list for the configured research providers and public pages;
4. temporary cache/output storage that can be inspected and discarded.

Do not mount a home directory, SSH directory, cloud configuration, browser
profile, unrelated datasets, Git credentials, or the host container socket. A
container provides the missing read boundary; the runtime's workspace sandbox
then supplies a useful second layer. Keep the safe launcher default inside the
container—`--unsafe-unattended` is for compatibility testing, not a requirement.

For an attended run, changing the runtime approval policy to an interactive mode
adds human review but prevents fully unattended batching. For a public package,
the current non-interactive, sandboxed default is the more reproducible choice;
the container/VM recommendation is the stronger confidentiality choice.

