#!/bin/bash

# S3 buckets
#aws s3 mb s3://my-bucket

# SQS queues
#aws sqs create-queue --queue-name my-queue

aws s3 mb --endpoint-url=http://localhost:4566 s3://rpa-ai-guidance-hub-assets
