import unittest

from services.batch_assessment import score_evidence


SUBJECTS = ["电场", "磁场运动", "牛顿运动定律", "功与能量", "未分类"]


def point(name, subject="电场"):
    return {"name": name, "subject": subject}


def step(identifier="s1", dependencies=(), conclusion="根据场强求电场力"):
    return {"id": identifier, "depends_on": list(dependencies), "conclusion": conclusion}


def route(**overrides):
    return {"core_subject": "电场", "points": [point("电场强度")], "steps": [],
            "coupling": 0, "novelty": 0, "reason": "题设直接给出了场强定义所需的物理量。",
            "physics_methods": ["比值定义法"], **overrides}


def part(label, *routes, required=True):
    return {"label": label, "required": required, "routes": list(routes)}


def score(*parts, has_solution=True, **rest):
    return score_evidence({"parts": list(parts), **rest}, SUBJECTS, has_solution=has_solution)


class BatchAssessmentTests(unittest.TestCase):
    def test_rounding_boundaries_and_metadata_preferences(self):
        result = score(part("1", route()), tags=["基础", "AI原创", "基础"])
        self.assertEqual(result["difficulty"], "0.5")
        self.assertEqual(result["tags"], "基础")
        self.assertEqual(result["physics_methods"], "比值定义法")
        result = score(part("1", route(steps=[step()])))
        self.assertEqual(result["difficulty"], "1.0")
        self.assertEqual(result["physics_methods"], "")
        # With raw 200 the exact rating is 3.25. Choose the upper half-star.
        nodes = [step("a"), step("b", ["a"])]
        points = [point(n) for n in ("电势", "电势差", "电场强度")]
        result = score(part("1", route(points=points, steps=nodes, coupling=2, novelty=2)))
        self.assertEqual(result["difficulty"], "3.5")
        nodes.extend([step("c", ["b"]), step("d", ["c"]), step("e", ["d"])])
        points.extend([point("电势能"), point("电场力做功")])
        result = score(part("1", route(points=points, steps=nodes, coupling=4, novelty=4)))
        self.assertEqual(result["difficulty"], "6.0")

    def test_longest_path_ignores_parallel_count_and_step_order(self):
        nodes = [step("end", ["left", "right"], "结合两条关系求速度"),
                 step("left", ["root"]), step("right", ["root"]), step("root")]
        result = score(part("1", route(steps=nodes)))
        details = result["parts"][0]
        self.assertEqual(details["S"], 3)
        self.assertEqual(len(details["chain"]), 3)
        self.assertEqual(details["chain"][-1], "结合两条关系求速度")
        self.assertEqual(score(part("1", route(steps=[step(str(i)) for i in range(8)])))
                         ["parts"][0]["S"], 1)
        for count, expected in enumerate([0, 1, 2, 3, 3, 4, 4]):
            with self.subTest(count=count):
                chain = [step(str(i), [str(i - 1)] if i else []) for i in range(count)]
                self.assertEqual(score(part("1", route(steps=chain)))["parts"][0]["S"], expected)

    def test_aliases_do_not_inflate_knowledge_counts(self):
        points = [point("牛二", "牛顿运动定律"), point("牛顿第二定律", "牛顿运动定律"),
                  point("牛顿第二运动定律", "牛顿运动定律"), point("牛顿 第二定律", "牛顿运动定律")]
        result = score(part("1", route(core_subject="牛顿运动定律", points=points)))
        self.assertEqual(result["knowledge_points"], "牛顿第二定律")
        self.assertEqual(result["parts"][0]["K"], 0)
        for count in range(1, 7):
            with self.subTest(count=count):
                points = [point("知识点" + str(i)) for i in range(count)]
                self.assertEqual(score(part("1", route(points=points)))["parts"][0]["K"], min(count - 1, 4))

    def test_lowest_legal_route_optional_work_and_required_point_union(self):
        first = route(steps=[step()])
        alternate = route(points=[point("动能定理", "功与能量")], steps=[step("a"), step("b", ["a"])])
        second = route(core_subject="磁场运动", points=[point("洛伦兹力", "磁场运动")])
        result = score(part("1", alternate, first), part("2", second),
                       part("拓展", alternate, required=False))
        self.assertEqual(result["difficulty"], "1.0")
        self.assertEqual(result["knowledge_points"], "电场强度，洛伦兹力")
        self.assertEqual(result["subject"], "电场，磁场运动")
        self.assertEqual(len(result["parts"]), 2)

    def test_multiple_prior_results_join_once_and_only_on_consuming_branches(self):
        first = route(steps=[step("a"), step("b", ["a"])])
        second = route(core_subject="功与能量", points=[point("动能定理", "功与能量")],
                       steps=[step("u", ["@1"])], coupling=1)
        third = route(core_subject="磁场运动", points=[point("洛伦兹力", "磁场运动")],
                      steps=[step("v", ["@2", "@1"]), step("x"), step("y", ["x"])], coupling=2)
        result = score(part("1", first), part("2", second), part("3", third))
        details = result["parts"][-1]
        self.assertEqual(len(details["chain"]), 4)
        self.assertEqual((details["K"], details["S"], details["C"]), (2, 3, 2))
        self.assertEqual(details["dependencies"], ["2", "1"])
        self.assertEqual(result["subject"], "磁场运动，电场，功与能量")

    def test_primary_subject_ties_respect_coverage_then_question_order(self):
        magnetic = route(core_subject="磁场运动", points=[point("洛伦兹力", "磁场运动")])
        self.assertEqual(score(part("1", magnetic), part("2", route()))["subject"], "磁场运动，电场")
        self.assertEqual(score(part("1", magnetic), part("2", route()), part("3", route()))
                         ["subject"], "电场，磁场运动")
        # Different raw totals can round to the same visible half star.
        one_step = route(core_subject="磁场运动", points=[point("洛伦兹力", "磁场运动")],
                         steps=[step()], novelty=1)
        electric = route(steps=[step()])
        self.assertEqual(score(part("1", one_step), part("2", electric), part("3", electric))
                         ["subject"], "电场，磁场运动")

    def test_status_follows_solution_support_and_insufficiency(self):
        self.assertEqual(score(part("1", route()))["status"], "正式")
        self.assertEqual(score(part("1", route()), has_solution=False)["status"], "暂评")
        self.assertEqual(score(part("1", route(inferred=True)))["status"], "暂评")
        result = score(insufficient_reason="引用的电场图缺失，无法还原关键条件。", known_points=[point("电势")])
        self.assertEqual((result["status"], result["difficulty"], result["subject"],
                          result["knowledge_points"]), ("待评", "", "电场", "电势"))
        self.assertIn("关键条件", result["reason"])
        # Partial evidence may keep tags but cannot yield an overall difficulty.
        result = score(part("1", route()), part("2"), insufficient_reason="第二问题干缺失。")
        self.assertEqual((result["difficulty"], len(result["parts"])), ("", 1))

    def test_invalid_model_responses_are_retryable_errors(self):
        malformed = [
            None, {}, {"parts": []}, {"parts": [part("1")]}, {"parts": [part("1", route())] * 2},
            {"parts": [part("1", route(core_subject="未分类"))]},
            {"parts": [part("1", route(points=[point("电势", "伪分类")]))]},
            {"parts": [part("1", route(points=[]))]},
            {"parts": [part("1", route(coupling=True))]},
            {"parts": [part("1", route(coupling=1))]},
            {"parts": [part("1", route(novelty=7))]},
            {"parts": [part("1", route(steps=[step("a"), step("a")]))]},
            {"parts": [part("1", route(steps=[step("a", ["missing"])]))]},
            {"parts": [part("1", route(steps=[step("a", ["b"]), step("b", ["a"])]))]},
            {"parts": [part("1", route(steps=[step("a", ["@2"])])), part("2", route())]},
            {"parts": [part("1", route(), required=False), part("2", route(steps=[step("a", ["@1"])]))]},
            {"parts": [part("1", route(reason=""))]},
        ]
        for evidence in malformed:
            with self.subTest(evidence=evidence), self.assertRaises(ValueError):
                score_evidence(evidence, SUBJECTS, has_solution=True)


if __name__ == "__main__":
    unittest.main()
