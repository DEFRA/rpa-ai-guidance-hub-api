#!/bin/bash

# S3 buckets

# Where cdp-uploader delivers an uploaded .docx.
aws s3 mb s3://rpa-ai-guidance-hub-source-docs || true

# Every converted document, its versions and its pictures, in one bucket. Not
# versioned: each version is written under a key of its own, and each picture under
# the digest of its bytes, so no key ever holds anything but what it first held.
aws s3 mb s3://rpa-ai-guidance-hub-managed-docs || true

# SQS queues
#aws sqs create-queue --queue-name my-queue
