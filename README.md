# mbta-server

Serverless API behind the [MBTA pixel train display](https://github.com/abbykatz23/mbta-display)
project. Stores user-submitted train sprites and relays live display state between
the Raspberry Pi and the web frontend.

Part of three repos: [mbta-display](https://github.com/abbykatz23/mbta-display) (Pi
client) · **mbta-server** (this repo) ·
[mbta-frontend](https://github.com/abbykatz23/mbta-frontend) (submission site).

You can submit a train right now at [makeatrain.pre-idea.com](https://makeatrain.pre-idea.com)
(or [makeatrain.netlify.app](https://makeatrain.netlify.app) in case I don't renew my domain subscription hehe).
DO IT!!! IT'S SO FUN!!!

**The meta-display repo has the most thorough readme**

## Endpoints

| Route | Auth | Purpose |
|---|---|---|
| `POST /submit` | — | Frontend submits a hand-drawn pixel sprite (name, birthday, PNG data URL). Validates dimensions/size and a Turnstile captcha token. |
| `GET /submissions` | — | Public list of approved submissions, powers the gallery page. |
| `GET /special-trains` | — | List of the hardcoded monthly/one-off trains. |
| `GET /sprites`, `GET /sprite-ids` | Pi key | Used by the Pi's sync job to pull down newly approved sprites and prune deleted ones. |
| `PATCH /submissions/{id}`, `DELETE /submissions/{id}` | admin key | Edit or remove a submission from the Gallery's admin mode. |
| `POST /queue/{id}`, `POST /queue-special/{name}` | admin key | Force a specific sprite to play next on the physical display. |
| `PUT /display-state`, `GET /display-state` | write: Pi key, read: — | The Pi pushes its current predictions/animation state every poll cycle; the frontend polls it to render a live simulation. |

## Stack

FastAPI + [Mangum](https://github.com/jordaneremieff/mangum) (ASGI on Lambda),
deployed with AWS SAM. S3 stores sprite PNGs; DynamoDB stores submissions and display state.

## Deploying

Requires three SSM parameters to exist first (referenced in `template.yaml`):
`/mbta/pi_api_key`, `/mbta/allowed_origin`, and `/mbta/turnstile_secret_key`.

```bash
sam build
sam deploy --guided   # first time
```

Once set up, `./deploy.ps1` builds and pushes straight to the existing Lambda
function without a full stack deploy.

## Local dev

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

Set `ALLOWED_ORIGINS`, `S3_BUCKET`, `DYNAMODB_TABLE`, `PI_API_KEY`,
`TURNSTILE_SECRET_KEY` in the environment, then:

```bash
uvicorn app:app --reload
```

## License

MIT — see [LICENSE](LICENSE).
