#!/bin/bash

# S3 buckets

# Where cdp-uploader delivers an uploaded .docx.
aws s3 mb s3://rpa-ai-guidance-hub-source-docs || true

# One Markdown file per converted guide, written to the same key each time it is
# converted — so the bucket's versions of that key are the guide's history.
aws s3 mb s3://rpa-ai-guidance-hub-managed-docs || true
aws s3api put-bucket-versioning \
  --bucket rpa-ai-guidance-hub-managed-docs \
  --versioning-configuration Status=Enabled || true

# The pictures those guides draw. Deliberately not versioned: an asset is named by
# the digest of its own bytes, so it is never written twice with anything different.
aws s3 mb s3://rpa-ai-guidance-hub-managed-doc-assets || true

# SQS queues
#aws sqs create-queue --queue-name my-queue
