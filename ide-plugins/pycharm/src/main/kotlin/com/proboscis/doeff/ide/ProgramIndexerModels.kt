package com.proboscis.doeff.ide

import com.google.gson.annotations.SerializedName

enum class IndexEntryCategory {
    @SerializedName("program_interpreter") PROGRAM_INTERPRETER,
    @SerializedName("program_transformer") PROGRAM_TRANSFORMER,
    @SerializedName("kleisli_program") KLEISLI_PROGRAM,
    @SerializedName("interceptor") INTERCEPTOR,
    @SerializedName("do_function") DO_FUNCTION,
    @SerializedName("accepts_program_param") ACCEPTS_PROGRAM_PARAM,
    @SerializedName("returns_program") RETURNS_PROGRAM,
    @SerializedName("accepts_effect_param") ACCEPTS_EFFECT_PARAM,
    @SerializedName("has_marker") HAS_MARKER,
    UNKNOWN;

    companion object {
        fun fromRaw(raw: String): IndexEntryCategory = when (raw) {
            "program_interpreter" -> PROGRAM_INTERPRETER
            "program_transformer" -> PROGRAM_TRANSFORMER
            "kleisli_program" -> KLEISLI_PROGRAM
            "interceptor" -> INTERCEPTOR
            "do_function" -> DO_FUNCTION
            "accepts_program_param" -> ACCEPTS_PROGRAM_PARAM
            "returns_program" -> RETURNS_PROGRAM
            "accepts_effect_param" -> ACCEPTS_EFFECT_PARAM
            "has_marker" -> HAS_MARKER
            else -> UNKNOWN
        }
    }
}

data class IndexParameter(
    val name: String,
    val annotation: String?,
    @SerializedName("is_required") val isRequired: Boolean,
    val position: Int,
    val kind: ParameterKind,
)

enum class ParameterKind {
    @SerializedName("positional_only") POSITIONAL_ONLY,
    @SerializedName("positional") POSITIONAL,
    @SerializedName("var_arg") VAR_ARG,
    @SerializedName("keyword_only") KEYWORD_ONLY,
    @SerializedName("var_keyword") VAR_KEYWORD
}

data class ProgramTypeUsage(
    val kind: ProgramUsageKind,
    val raw: String,
    @SerializedName("type_arguments") val typeArguments: List<String>,
)

enum class ProgramUsageKind {
    @SerializedName("program") PROGRAM,
    @SerializedName("kleisli_program") KLEISLI_PROGRAM,
}

data class IndexEntry(
    val name: String,
    @SerializedName("qualified_name") val qualifiedName: String,
    @SerializedName("file_path") val filePath: String,
    val line: Int,
    val categories: List<String>,
    @SerializedName("program_parameters") val programParameters: List<IndexParameter>,
    @SerializedName("program_interpreter_parameters") val interpreterParameters: List<IndexParameter>,
    @SerializedName("type_usages") val typeUsages: List<ProgramTypeUsage>,
    val docstring: String?,
    val markers: List<String>? = null,  // Made nullable to handle missing field in older index data
) {
    fun hasCategory(category: IndexEntryCategory): Boolean =
        categories.any { IndexEntryCategory.fromRaw(it) == category }
    
    fun hasMarker(marker: String): Boolean =
        markers?.any { it.equals(marker, ignoreCase = true) } ?: false
}

data class IndexPayload(val entries: List<IndexEntry>)

fun ProgramTypeUsage.matchesType(typeName: String): Boolean {
    if (typeName.equals("Any", ignoreCase = true)) {
        return true
    }
    if (raw.equals(typeName, ignoreCase = true)) {
        return true
    }
    return typeArguments.any { it.equals(typeName, ignoreCase = true) }
}
