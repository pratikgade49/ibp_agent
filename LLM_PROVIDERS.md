# LLM Provider Configuration

Both the IBP agent and the S/4 capacity agent use the shared provider selected by `LLM_PROVIDER`.
Set this environment variable and the matching provider settings before starting or redeploying the app.

## Amazon Bedrock

```dotenv
LLM_PROVIDER=bedrock
AWS_REGION=us-east-1
BEDROCK_MODEL=us.anthropic.claude-sonnet-4-6
AWS_BEARER_TOKEN_BEDROCK=<Bedrock API key>
```

The Bedrock provider uses the Converse API and supports tool calls. The model must be enabled for the AWS account and region. Boto3's AWS credential chain also works, so production deployments can use an IAM role or temporary credentials instead of a long-term API key. AWS recommends short-term credentials for production; long-term Bedrock API keys are intended for exploration and development.

For local use, keep credentials in the ignored `.env` file or another secret store. For Cloud Foundry, configure secrets with `cf set-env` or a bound secret service; do not commit keys into source or the deployment manifest.

## Other Providers

Switch providers by changing `LLM_PROVIDER`:

| Value | Required settings |
| --- | --- |
| `gemini` | `GOOGLE_API_KEY` or `GEMINI_API_KEY`; optional `GEMINI_MODEL` |
| `anthropic` | `ANTHROPIC_API_KEY`; optional `ANTHROPIC_MODEL` |
| `deepseek` | `DEEPSEEK_API_KEY`; optional `DEEPSEEK_MODEL` |
| `bedrock` | `AWS_REGION`, `BEDROCK_MODEL`, and either `AWS_BEARER_TOKEN_BEDROCK` or AWS SDK credentials |

After changing provider configuration in Cloud Foundry, restart the app so both agent graphs pick up the new setting.

The repository deployment script reads provider settings from the root `.cf-vars.yml` file and passes it to `cf push`. If the file is missing, `scripts/deploy.ps1` creates it from `.cf-vars.example.yml` and stops so you can fill in the values. The real vars file is excluded from Git and the Cloud Foundry upload; only values are interpolated into the app environment during push.

## S/4 Capacity Reader Connection

The S/4 capacity reader also requires these values in `.cf-vars.yml`:

```yaml
s4-base-url: https://your-s4-host:44301
s4-username: "your-read-only-s4-user"
s4-password: "your-s4-password"
s4-api-timeout-seconds: "30"
```

`s4-base-url` is the origin only; the reader appends the `API_WORK_CENTERS` OData service path. The deploy manifest maps these values to `S4_BASE_URL`, `S4_USERNAME`, `S4_PASSWORD`, and `S4_API_TIMEOUT_SECONDS`. If `.cf-vars.yml` already exists, add these entries to it before deploying. Keep the username/password file private; `.cf-vars.yml` is ignored by Git and excluded from the CF upload.

Quote username/password values in YAML, especially when they contain `#`, `:`, or other YAML-significant characters. `scripts/deploy.ps1` checks that all manifest variable keys are present before pushing and prints missing key names only, never their values.