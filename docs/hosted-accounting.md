# Hosted provider accounting

Hosted provider accounting is a Host callback around each metered model
attempt. The Agent does not contain pricing or billing policy; it sends the
request bounds to the Host before contacting the model and sends normalized
usage back after the attempt.

The beta hosted contract supports the exact `OpenAICompatProvider` adapter
class. This covers the supported DeepSeek and GPT text paths when they use the
OpenAI-compatible adapter. The callback rejects other provider classes before
making an authorization request, including `FallbackProvider`,
`AnthropicProvider`, `OpenAICodexProvider`, and other specialized or future
adapters.

Fallback chains are therefore not a hosted accounting configuration. A hosted
worker configured with a fallback wrapper fails closed until the chain is
removed or the Host and Agent contract is extended and reviewed together.

Local runs without a Host callback keep the generic provider accounting path
unchanged. The Web Host's reviewed price entry must identify the exact
`OpenAICompatProvider` class and model used by the worker.
