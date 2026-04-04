# Worker API

## Endpoints

- `GET /health`
- `POST /summarize`
- `POST /classify`
- `POST /draft_lessons`

## Example

```bash
curl -s http://127.0.0.1:8765/summarize \
  -H 'Content-Type: application/json' \
  -d '{"text":"P1 reviewed a failed run and found repeated timeout noise."}'
```

## Error handling

- invalid JSON: `400`
- missing required field: `400`
- Ollama unreachable or invalid response: `502`
- unexpected internal error: `500`
