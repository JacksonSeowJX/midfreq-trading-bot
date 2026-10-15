# Developer guide

How the repository is set up for development, how it is tested, how code reaches the machines
that run it, and how this site is built.

<div class="grid cards" markdown>

-   **[Environment](environment.md)**

    ---

    The uv workspace and its pinned versions, configuration and the runtime root, the web app,
    and the API's contract with it.

-   **[Testing](testing.md)**

    ---

    The hermetic default suite, the cross-package and opt-in real-data tests, the package-layer
    check, and the live-path replay.

-   **[CI workflows](ci.md)**

    ---

    What runs on every push, on a release, on a deploy, and on a change to the documentation.

-   **[Releases and deployment](releases_and_deployment.md)**

    ---

    The trading VM and its schedule, versioned releases and deploys, and the hosted dashboard,
    described by role.

-   **[Documentation](documentation.md)**

    ---

    How this site is built: MkDocs with Material, the repository-document hook, and the
    generated API reference.

</div>

## Conventions

- **One version.** Every package carries the same version, set by `ops/set_version.sh`.
- **Layers.** A package imports only the packages below it ([Architecture](../concepts/architecture.md#packages-and-their-layers)).
- **Hermetic tests.** The default suite never touches the network, a broker gateway, or the
  real `data/` and `live_sessions/` folders.
- **Live changes.** A change to the live trading path is replayed against the recorded sessions
  and checked for backtest equivalence before it is released to the trading VM.
- **Secrets.** No key, password or access list is ever committed; configuration names the
  environment variables that hold them.
