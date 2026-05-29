def test_prompts_module_exports_five_templates():
    from src import prompts
    assert isinstance(prompts.REVIEW_SYSTEM, str) and prompts.REVIEW_SYSTEM
    assert isinstance(prompts.REVIEW_USER_TEMPLATE, str) and prompts.REVIEW_USER_TEMPLATE
    assert isinstance(prompts.REVISE_SYSTEM, str) and prompts.REVISE_SYSTEM
    assert isinstance(prompts.REVISE_USER_TEMPLATE, str) and prompts.REVISE_USER_TEMPLATE
    assert isinstance(prompts.CAROUSEL_SYSTEM, str) and prompts.CAROUSEL_SYSTEM


def test_orchestrator_uses_prompts_registry():
    """The orchestrator must import templates from src.prompts, not redefine them."""
    import src.orchestrator as orch
    from src import prompts
    assert orch._REVIEW_SYSTEM is prompts.REVIEW_SYSTEM
    assert orch._REVIEW_USER is prompts.REVIEW_USER_TEMPLATE
    assert orch._REVISE_SYSTEM is prompts.REVISE_SYSTEM
    assert orch._REVISE_USER is prompts.REVISE_USER_TEMPLATE


def test_carousel_gen_uses_prompts_registry():
    import src.carousel_gen as cg
    from src import prompts
    assert cg._SYSTEM is prompts.CAROUSEL_SYSTEM
