package com.proboscis.doeff.ide

import com.intellij.openapi.diagnostic.Logger
import com.intellij.psi.PsiElement
import com.jetbrains.python.psi.PyAnnotation
import com.jetbrains.python.psi.PyReferenceExpression
import com.jetbrains.python.psi.PyStringLiteralExpression
import com.jetbrains.python.psi.PySubscriptionExpression

object ProgramTypeExtractor {
    private val logger = Logger.getInstance(ProgramTypeExtractor::class.java)

    data class Result(val typeArgument: String)

    fun extractProgramType(annotationElement: PsiElement?): Result? {
        var current: PsiElement? = annotationElement
        if (current is PyAnnotation) {
            current = current.value
        }

        when (current) {
            is PySubscriptionExpression -> {
                val operand = current.operand as? PyReferenceExpression ?: return null
                if (operand.referencedName != "Program") {
                    return null
                }
                val typeExpression = current.indexExpression ?: return Result("Any")
                val programType = typeExpression.text?.takeIf { it.isNotBlank() } ?: "Any"
                return Result(programType)
            }
            is PyReferenceExpression -> {
                if (current.referencedName == "Program") {
                    return Result("Any")
                }
            }
            is PyStringLiteralExpression -> {
                val value = current.stringValue
                if (value.startsWith("Program[")) {
                    val inner = value.removePrefix("Program[").removeSuffix("]")
                    return Result(inner.ifBlank { "Any" })
                }
                if (value == "Program") {
                    return Result("Any")
                }
            }
        }

        logger.debug("ProgramTypeExtractor: no Program type found for ${annotationElement?.text}")
        return null
    }
}
