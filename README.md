# mbta-server

Serverless API behind the [MBTA pixel train display](https://github.com/abbykatz23/mbta-display)
project. Stores user-submitted train sprites and relays live display state between
the Raspberry Pi and the web frontend.

Part of three repos: [mbta-display](https://github.com/abbykatz23/mbta-display) (Pi
client) · **mbta-server** (this repo) ·
[mbta-frontend](https://github.com/abbykatz23/mbta-frontend) (submission site).

## Endpoints

| Route | Auth | Purpose |
|---|---|---|
| `POST /submit` | — | Frontend submits a hand-drawn pixel sprite (name, birthday, PNG data URL). Validates dimensions/size, auto-approves. |
| `GET /submissions` | — | Public list of approved submissions, powers the gallery page. |
| `GET /special-trains` | — | List of the hardcoded monthly/one-off trains. |
| `GET /sprites`, `GET /sprite-ids` | Pi key | Used by the Pi's sync job to pull down newly approved sprites and prune deleted ones. |
| `PATCH /submissions/{id}`, `DELETE /submissions/{id}` | admin key | Edit or remove a submission from the Gallery's admin mode. |
| `POST /queue/{id}`, `POST /queue-special/{name}` | admin key | Force a specific sprite to play next on the physical display. |
| `PUT /display-state`, `GET /display-state` | write: Pi key, read: — | The Pi pushes its current predictions/animation state every poll cycle; the frontend polls it to render a live simulation. |

Auth is a single shared secret compared with `hmac.compare_digest` against the
`X-API-Key` header — used both by the Pi (server-to-server) and by admin actions in
the Gallery UI.

## Stack

FastAPI + [Mangum](https://github.com/jordaneremieff/mangum) (ASGI on Lambda),
deployed with AWS SAM. S3 stores sprite PNGs; DynamoDB (single table, on-demand
billing) stores submissions and display state.

## Deploying

Requires two SSM parameters to exist first (referenced in `template.yaml`):
`/mbta/pi_api_key` and `/mbta/allowed_origin`.

```bash
sam build
sam deploy --guided   # first time
```

Once set up, `./deploy.ps1` builds and pushes straight to the existing Lambda
function without a full stack deploy.

## Local dev

```bash
pip install -r requirements.txt
```

Set `ALLOWED_ORIGINS`, `S3_BUCKET`, `DYNAMODB_TABLE`, `PI_API_KEY` in the
environment, then:

```bash
uvicorn app:app --reload
```

## License

MIT — see [LICENSE](LICENSE).
