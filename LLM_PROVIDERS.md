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

Deploy with the repository wrapper so required service instances are created before the app push:

```powershell
.\scripts\deploy.ps1
```

The script creates the `ibp-connectivity` and `ibp-destination` instances on their `lite` plans when they are missing, waits for provisioning, then runs `cf push` using `manifest.yaml`. The manifest binds both services to the app. A bare `cf push --vars-file .cf-vars.yml --redact-env` cannot create managed service instances; Cloud Foundry CLI has no manifest pre-push provisioning hook.

## S/4 Capacity Reader Connection

The app uses the `S3DTEST` OnPremise destination with Basic authentication. Configure that destination in the BTP Destination service and configure the matching virtual host, port, and allowed OData resources in Cloud Connector. The deployment wrapper provisions and binds the Connectivity and Destination service instances, but it cannot create the Cloud Connector mapping or the S/4 destination configuration; those remain administrator-managed BTP setup.

Set `s4-api-timeout-seconds` in `.cf-vars.yml` as needed. S/4 URL and Basic credentials are read from `S3DTEST`, not supplied in the vars file. The vars file should contain only the selected LLM provider's credentials and app-level settings; keep it private and never commit secrets.

The script checks that required vars keys exist and prints missing key names only, never their values. Ensure the CLI is logged in and targeted to the intended org and space before deployment. The space must have access to the `connectivity/lite` and `destination/lite` plans, and the configured `S3DTEST` destination must already exist and pass its BTP connection check.