# Feishu Office Memory Demo Console

This is the local recording console for the Feishu enterprise memory-engine
delivery. It uses the Linear-style design system from
`/Users/benjiwinchester/Desktop/awesome-design-md/design-md/linear.app/DESIGN.md`.

The console carries the commercial launch identity:

- `RuyiAI-Stack`
- `https://ruyiai-stack.github.io`
- `RuyiAI-Stack/ruyiai-stack.github.io`

Run it after the competition runtime is available on `127.0.0.1:18100`:

```bash
cd competition/feishu_office/demo_console
export LARK_MEMORY_CORE_API_KEY="$(cat ../../../.run/feishu-office-competition/runtime/api_key.txt)"
npm install
npm run dev
```

The Vite proxy injects the API key server-side. Browser code only calls `/api/*`
and does not receive the key. The UI reads real evidence and memory APIs and
does not render assistant answer bodies.
