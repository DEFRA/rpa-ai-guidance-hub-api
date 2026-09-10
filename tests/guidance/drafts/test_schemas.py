from app.guidance.drafts.schemas import UploadCallbackPayload, UploadedDocument

# The real cdp-uploader callback body.
CALLBACK_BODY = {
    "uploadStatus": "ready",
    "metadata": {"customerId": "1234"},
    "form": {
        "button": "upload",
        "file": {
            "fileId": "9fcaabe5-77ec-44db-8356-3a6e8dc51b13",
            "filename": "photo.jpeg",
            "contentType": "image/jpeg",
            "detectedContentType": "image/jpeg",
            "contentLength": 11264,
            "checksumSha256": "bng5jOVC6TxEgwTUlX4DikFtDEYEc8vQTsOP0ZAv21c=",
            "fileStatus": "complete",
            "s3Key": "scanned/3b0b2a02-a669-44ba-9b78-bd5cb8460253/9fcaabe5-77ec-44db-8356-3a6e8dc51b13",
            "s3Bucket": "cdp-example-node-frontend",
        },
    },
    "numberOfRejectedFiles": 0,
}


class TestUploadedDocument:
    def test_projects_the_completed_file(self):
        payload = UploadCallbackPayload.model_validate(CALLBACK_BODY)

        documents = payload.uploaded_documents()

        assert documents == [
            UploadedDocument(
                file_id="9fcaabe5-77ec-44db-8356-3a6e8dc51b13",
                s3_key="scanned/3b0b2a02-a669-44ba-9b78-bd5cb8460253/9fcaabe5-77ec-44db-8356-3a6e8dc51b13",
            )
        ]

    def test_ignores_non_file_form_fields(self):
        """`form.button` has no `fileId` and must not be mistaken for a file."""
        payload = UploadCallbackPayload.model_validate(CALLBACK_BODY)

        documents = payload.uploaded_documents()

        assert documents

    def test_returns_empty_when_no_file_has_completed(self):
        body = {
            **CALLBACK_BODY,
            "form": {
                "file": {**CALLBACK_BODY["form"]["file"], "fileStatus": "pending"}
            },
        }
        payload = UploadCallbackPayload.model_validate(body)

        assert payload.uploaded_documents() == []

    def test_returns_empty_with_no_file_field_at_all(self):
        payload = UploadCallbackPayload.model_validate(
            {**CALLBACK_BODY, "form": {"button": "upload"}}
        )

        assert payload.uploaded_documents() == []

    def test_projects_every_completed_file_from_a_multi_file_field(self):
        """AC: multiple files are possible - a field's value can be a list, and
        each completed file gets its own document, keyed by its own file_id."""
        first = {**CALLBACK_BODY["form"]["file"], "fileId": "first"}
        second = {**CALLBACK_BODY["form"]["file"], "fileId": "second"}
        payload = UploadCallbackPayload.model_validate(
            {**CALLBACK_BODY, "form": {"file": [first, second]}}
        )

        documents = payload.uploaded_documents()

        assert [document.file_id for document in documents] == ["first", "second"]

    def test_ignores_extra_fields_on_the_payload(self):
        """A future cdp-uploader field is not this endpoint's problem."""
        UploadCallbackPayload.model_validate({**CALLBACK_BODY, "somethingNew": True})
