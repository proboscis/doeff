package com.proboscis.doeff.ide

import com.intellij.codeInsight.hints.ChangeListener
import com.intellij.codeInsight.hints.FactoryInlayHintsCollector
import com.intellij.codeInsight.hints.ImmediateConfigurable
import com.intellij.codeInsight.hints.InlayHintsCollector
import com.intellij.codeInsight.hints.InlayHintsProvider
import com.intellij.codeInsight.hints.InlayHintsSink
import com.intellij.codeInsight.hints.NoSettings
import com.intellij.codeInsight.hints.SettingsKey
import com.intellij.codeInsight.hints.presentation.MouseButton
import com.intellij.openapi.fileEditor.FileDocumentManager
import com.intellij.openapi.editor.Editor
import com.intellij.psi.PsiElement
import com.intellij.psi.PsiFile
import com.intellij.psi.util.PsiTreeUtil
import com.jetbrains.python.psi.PyClass
import com.jetbrains.python.psi.PyTargetExpression
import javax.swing.JComponent
import javax.swing.JPanel

class DoeffRunLensInlayProvider : InlayHintsProvider<NoSettings> {
    override val key: SettingsKey<NoSettings> = SettingsKey("doeff.runLens")
    override val name: String = "doeff Run Lens"
    override val previewText: String =
        """
        from doeff import Program

        user_program: Program["User"] = ...
        """.trimIndent()

    override fun createSettings(): NoSettings = NoSettings()

    override fun createConfigurable(settings: NoSettings): ImmediateConfigurable =
        object : ImmediateConfigurable {
            override fun createComponent(listener: ChangeListener): JComponent = JPanel()
        }

    override fun getCollectorFor(file: PsiFile, editor: Editor, settings: NoSettings, sink: InlayHintsSink): InlayHintsCollector {
        return object : FactoryInlayHintsCollector(editor) {
            override fun collect(element: PsiElement, editor: Editor, sink: InlayHintsSink): Boolean {
                val targetExpression = element as? PyTargetExpression ?: return true

                if (PsiTreeUtil.getParentOfType(targetExpression, PyClass::class.java) != null) {
                    return true
                }

                val annotation = targetExpression.annotation ?: return true
                val extractedType = ProgramTypeExtractor.extractProgramType(annotation)
                val typeArgument = when {
                    extractedType != null -> extractedType.typeArgument
                    annotation.text.contains("Program") -> "Any"
                    else -> return true
                }

                val document = editor.document
                val line = document.getLineNumber(targetExpression.textOffset)
                val lineStartOffset = document.getLineStartOffset(line)

                val factory = factory
                val label = factory.roundWithBackground(factory.smallText("Run doeff"))
                val clickable = factory.onClick(label, MouseButton.Left) { _, _ ->
                    FileDocumentManager.getInstance().saveAllDocuments()
                    ProgramExecutionController.handleNavigation(null, targetExpression, typeArgument)
                }

                sink.addBlockElement(lineStartOffset, false, true, 0, clickable)
                return true
            }
        }
    }
}

