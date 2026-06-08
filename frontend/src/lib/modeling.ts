export interface ModelingFieldNormalized {
  name: string
  type: string
  isPrimaryKey?: boolean
  primaryKey?: boolean
  display_name?: string
  description?: string
}

export function normalizeModelFields(entity: any): ModelingFieldNormalized[] {
  const fallback = (entity?.column_defs ?? entity?.columns ?? []).map((column: any) => ({
    name: column.name,
    type: column.type,
    isPrimaryKey: Boolean(column.is_primary_key || column.primaryKey || column.isPrimaryKey),
    primaryKey: Boolean(column.is_primary_key || column.primaryKey || column.isPrimaryKey),
    display_name: column.display_name,
    description: column.description,
  }))
  const source = entity?.fields && entity.fields.length > 0 ? entity.fields : fallback
  return source
    .filter((field: any) => Boolean(field?.name))
    .map((field: any) => ({
      name: field.name,
      type: field.type ?? 'UNKNOWN',
      isPrimaryKey: Boolean(field.isPrimaryKey || field.primaryKey || field.is_primary_key),
      primaryKey: Boolean(field.isPrimaryKey || field.primaryKey || field.is_primary_key),
      display_name: field.display_name,
      description: field.description,
    }))
}

export function normalizeRelationFields(model: any, calculatedFields: any[]): Array<{
  name: string
  type: string
  isCalculated?: boolean
}> {
  const modelFields = normalizeModelFields(model).map((field) => ({
    name: field.name,
    type: field.type,
    isCalculated: false,
  }))
  const calcFields = calculatedFields
    .filter((field) => Boolean(field?.name))
    .map((field) => ({
      name: field.name,
      type: field.result_type ?? 'CALCULATED',
      isCalculated: true,
    }))
  const seen = new Set<string>()
  return [...modelFields, ...calcFields].filter((field) => {
    const key = field.name
    if (!key || seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export function getModelLabel(model: any, fallbackPrefix = 'Model') {
  return model?.display_name ?? model?.name ?? `${fallbackPrefix} ${model?.id ?? ''}`.trim()
}
