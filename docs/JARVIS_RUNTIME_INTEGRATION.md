Автохирург-Jarvis — runtime integration note

Migration branch: jarvis-gpt-migration

The approved core prompt is stored in docs/JARVIS_CORE_PROMPT_RU.md and must be used as the canonical system prompt when connecting the migrated Custom GPT logic to the Jarvis runtime.

Runtime rule: load the canonical prompt, then expose the existing Avtohirurg MCP functions. Do not duplicate or invent Custom GPT instructions that have not been recovered from the source GPT.

Evidence-first flow: FACTS -> HYPOTHESES -> CHECKS -> CONFIRMATION -> ACTION -> RESULT VERIFICATION.

Production rule: do not deploy this migration branch to production until runtime smoke tests pass.