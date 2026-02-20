package com.proboscis.doeff.ide

import com.intellij.psi.util.PsiTreeUtil
import com.intellij.testFramework.fixtures.BasePlatformTestCase
import com.jetbrains.python.psi.PyTargetExpression

class ProgramGutterIconProviderTest : BasePlatformTestCase() {
    fun testProgramAnnotationProducesGutterIcon() {
        val file = myFixture.configureByText(
            "sample.py",
            """
            from doeff import Program

            program: Program[int] = Program.pure(1)
            """.trimIndent()
        )

        val target = PsiTreeUtil.collectElementsOfType(file, PyTargetExpression::class.java)
            .first { it.annotation != null }
        val provider = ProgramGutterIconProvider()
        val marker = provider.getLineMarkerInfo(target.nameIdentifier!!)
        assertNotNull("Expected gutter icon for Program annotation", marker)
    }

    fun testStringAnnotationProducesGutterIcon() {
        val file = myFixture.configureByText(
            "sample.py",
            """
            from __future__ import annotations
            from doeff import Program

            program: "Program[int]" = Program.pure(1)
            """.trimIndent()
        )

        val target = PsiTreeUtil.collectElementsOfType(file, PyTargetExpression::class.java)
            .first { it.annotation != null }
        val provider = ProgramGutterIconProvider()
        val marker = provider.getLineMarkerInfo(target.nameIdentifier!!)
        assertNotNull("Expected gutter icon for string Program annotation", marker)
    }

    fun testSegmentationStyleAnnotationProducesGutterIcon() {
        val file = myFixture.configureByText(
            "segmentation_doeff.py",
            """
            from doeff import Program

            Img = object

            test_image_program:Program[Img] = Program.pure(object())
            test_aggregation_program: Program[Img] = Program.pure(object())
            test_aggregation_custom_program: "Program[Img]" = Program.pure(object())
            """.trimIndent()
        )

        val target = PsiTreeUtil.collectElementsOfType(file, PyTargetExpression::class.java)
        val provider = ProgramGutterIconProvider()
        val markers = target.mapNotNull { expr ->
            provider.getLineMarkerInfo(expr.nameIdentifier!!)
        }
        assertEquals(3, markers.size)
    }
}
