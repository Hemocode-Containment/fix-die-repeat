# Language and Platform Heuristics

Read this reference when choosing a language, runtime, or full technology stack. These are
preferred starting points because their strengths repeatedly matter in the corresponding problem
shapes. They are not mandatory mappings, an exhaustive catalog, or substitutes for requirements
analysis.

## Preferred starting points

- **Rust:** Performance, memory efficiency, predictable resource use, systems work,
  high-performance concurrency, and native binaries.
- **Elixir/BEAM:** Supervision, fault isolation, highly concurrent stateful systems, distributed
  processes, and long-running service lifecycle management.
- **TypeScript:** Application and web ecosystems, shared client/server types, broad integrations,
  and asynchronous API-heavy services.
- **Python:** AI and ML ecosystems, data and scientific computing, rapid experimentation, and
  ecosystem-dependent automation or tooling.
- **C#:** .NET ecosystems, desktop applications, game development, mature application frameworks,
  strong tooling, and systems where managed performance is sufficient.

## Platform-native escape

Prefer a platform-native technology when deep integration with that platform dominates the
requirements, unless another stack provides a compelling architectural advantage. This heuristic
should surface choices such as Swift for Apple-native work and Kotlin for Android or an existing
JVM-centered system without requiring a maintained list of every platform language.

Consider other languages, runtimes, frameworks, and platforms whenever their characteristics fit
the requirements better. Add another preferred heuristic only when repeated decisions show that
the agent keeps overlooking a technology whose strengths materially match the work.
