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
                assert evidence["metrics"]["duty_detection_rate"] == 1
