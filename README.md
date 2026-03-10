## Docker Deploy

```bash
chmod +x deploy.sh
ANTHROPIC_API_KEY=your_key ./deploy.sh
curl -X POST http://127.0.0.1:8000/notify -H "Content-Type: application/json" -d '{"pipeline":"default","message":{"content":"hello"}}'
```
