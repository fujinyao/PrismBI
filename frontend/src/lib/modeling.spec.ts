import { describe, expect, it } from 'vitest'
import { getModelLabel, normalizeModelFields, normalizeRelationFields } from './modeling'

describe('modeling helpers', () => {
  it('normalizes model fields and preserves primary key markers', () => {
    const normalized = normalizeModelFields({
      column_defs: [
        { name: 'id', type: 'INTEGER', is_primary_key: true },
        { name: 'name', type: 'VARCHAR', description: 'user name' },
      ],
    })

    expect(normalized).toEqual([
      {
        name: 'id',
        type: 'INTEGER',
        isPrimaryKey: true,
        primaryKey: true,
        display_name: undefined,
        description: undefined,
      },
      {
        name: 'name',
        type: 'VARCHAR',
        isPrimaryKey: false,
        primaryKey: false,
        display_name: undefined,
        description: 'user name',
      },
    ])
  })

  it('builds relation fields including calculated columns without duplicates', () => {
    const fields = normalizeRelationFields(
      {
        fields: [
          { name: 'order_id', type: 'INTEGER' },
          { name: 'total', type: 'DECIMAL' },
        ],
      },
      [
        { name: 'profit', result_type: 'DOUBLE' },
        { name: 'total', result_type: 'DOUBLE' },
      ],
    )

    expect(fields).toEqual([
      { name: 'order_id', type: 'INTEGER', isCalculated: false },
      { name: 'total', type: 'DECIMAL', isCalculated: false },
      { name: 'profit', type: 'DOUBLE', isCalculated: true },
    ])
  })

  it('returns model display label with expected fallback chain', () => {
    expect(getModelLabel({ display_name: 'Sales', name: 'sales' })).toBe('Sales')
    expect(getModelLabel({ name: 'orders' })).toBe('orders')
    expect(getModelLabel({ id: 9 }, 'Entity')).toBe('Entity 9')
  })
})
