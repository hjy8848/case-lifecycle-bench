from case_lifecycle_bench.template_check import TEMPLATES, validate_template


def test_new_workflows_generate_and_pass_the_common_gate():
    for name in ("credit-delinquency", "credit-application", "field-service-repair"):
        spec = TEMPLATES[name]
        report = validate_template(spec, 300)
        assert report["deterministic_passed"], name
        assert len(report["generated"]) == 10
        assert len(report["counterexamples"]) == 6
        for generated in report["generated"]:
            assert generated["case"] == spec.generate(generated["seed"])
            for evidence in generated["oracle"].values():
                # Detection is measured on event-type duties only. An instance whose only
                # obligation is a deadline the agent works out has no event to detect.
                rate = evidence["metrics"]["duty_detection_rate"]
                assert rate == 1 or (rate is None and not evidence["metrics"]["duty_events"]), (
                    name,
                    generated["seed"],
                )


import unittest  # noqa: E402


class CommonGateTest(unittest.TestCase):
    """unittest discovery skips plain test functions, so run this one through a TestCase."""

    def test_gate(self):
        test_new_workflows_generate_and_pass_the_common_gate()
