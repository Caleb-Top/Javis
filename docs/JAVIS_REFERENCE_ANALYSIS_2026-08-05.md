# Javis v3 reference package analysis (2026-08-05)

## Scope

Reviewed the four user-supplied archives without executing untrusted installers:

- `chatbox-main.zip`
- `Kimi-K3-main.zip`
- `Kimi-K3-feature-multimodal-ai-upstream.zip`
- `WaveBench-master.zip`

The review focused on architecture and test patterns that can be independently
reimplemented in Javis. No third-party source was copied into the product.

## Chatbox

Useful patterns:

- Provider definitions are separated from provider construction, which makes
  it straightforward to keep multiple model profiles and switch routes.
- Model metadata is cached instead of being rediscovered for every request.
- Tool calls tolerate and repair common JSON formatting errors.
- Approval is a persistent stop condition rather than a transient dialog.
- Expected provider errors are classified separately from application faults.
- Backups and operation logs are sanitized so API keys are not exported.
- Update and Windows sandbox paths have explicit fallbacks.

Javis application:

- Live and Code now use one settings surface with two explicit routes.
- Provider secrets remain provider-scoped while each route can choose a
  different provider and model.
- Model installation is a plan/approve/execute workflow.

License note: the reviewed Chatbox tree is GPLv3. Javis uses the architectural
ideas only; source code was not copied.

## Kimi K3

`Kimi-K3-main.zip` contains documentation and a 47-page technical report, not a
local inference implementation. The report describes a 2.8T-parameter MoE
model with 104B active parameters, native text/image/video input, long context,
and a unified agent-training harness. This is not a practical model payload for
the Javis Windows installer.

`Kimi-K3-feature-multimodal-ai-upstream.zip` adds a small OpenAI-compatible
cloud API wrapper and interactive smoke scripts. It does not add K3 weights or
a local runtime. The scripts compile but do not provide assertion-based,
offline regression coverage; image/video requests also embed complete files as
base64, which is unsuitable as Javis's general media pipeline.

Useful patterns:

- Treat multimodality as a route capability, not a separate conversation.
- Preserve long-running session state with pause, resume, fork and snapshot.
- Prefer verifiable task outcomes and sandbox state over self-reported success.
- Separate personal-assistant requests from autonomous-execution evaluations.

License note: Kimi K3 uses a custom model license with commercial conditions.
No model assets or source were imported.

## WaveBench

WaveBench has the most directly reusable engineering structure and is MIT
licensed.

Useful patterns:

- Declarative run plans with separate `check`, `verify` and execute phases.
- Explicit opt-in for risky actions.
- Snapshot/restore performed in a `finally` path.
- Capability-gated drivers and plugins.
- Atomic evidence artifacts and a timestamped execution timeline.
- Local-only, read-only control surfaces by default.
- Package-level dry-run, doctor and recovery commands.

Javis application:

- The local-model wizard first produces a read-only plan showing source,
  destination, size, license/gating state and runtime readiness.
- Extraction and downloads require an explicit confirmation marker.
- Offline payloads are size- and SHA-256-locked and ZIP paths are checked before
  extraction.
- Hugging Face installation is resumable and uses a staging file before
  promotion.

## Resulting release design

1. `Javis-v3.0.0-Setup.exe` contains the desktop application, Python runtime,
   voice stack and five core systems. It contains no Ollama runtime or LLM
   weights.
2. `Javis-R1-8B-Addon` is an optional adjacent folder containing portable
   Ollama, DeepSeek R1 8B and a signed-by-hash manifest.
3. Settings opened from Live or Code are the same surface. Users may share one
   route or independently select local/cloud providers for each surface.
4. The model wizard can import the offline add-on into a user-selected
   directory. It can also search Hugging Face for GGUF repositories, show a
   no-write plan, download only after consent, and adapt the GGUF through
   portable Ollama.

