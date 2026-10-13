from app.reference import labels


class TestLabelled:
    def test_replaces_each_reference_key_with_its_label_in_order(self):
        metadata = {
            "schemes": ["not-specific", "basic-payment-scheme"],
            "systems": ["crm", "siti-agri"],
            "audience": ["customer"],
            "guidanceType": ["process-guide"],
        }

        assert labels.labelled(metadata) == {
            "schemes": ["Not scheme-specific", "Basic Payment Scheme"],
            "systems": ["CRM", "Siti Agri"],
            "audience": ["Customer"],
            "guidanceType": ["Process guide"],
        }

    def test_labels_the_none_scheme_the_front_end_stores(self):
        assert labels.labelled({"schemes": ["none"]}) == {
            "schemes": ["Not scheme-specific"]
        }

    def test_leaves_a_key_that_is_not_in_the_reference_data(self):
        assert labels.labelled({"systems": ["crm", "retired"]}) == {
            "systems": ["CRM", "retired"]
        }

    def test_passes_through_everything_else(self):
        metadata = {
            "title": "A guide",
            "owner": "Team",
            "guidanceType": "process",
            "audience": None,
        }

        assert labels.labelled(metadata) == metadata

    def test_does_not_change_what_it_was_given(self):
        metadata = {"systems": ["crm"]}

        labels.labelled(metadata)

        assert metadata == {"systems": ["crm"]}
