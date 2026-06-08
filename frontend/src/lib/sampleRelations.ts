export type SampleRelationType = 'MANY_TO_ONE' | 'ONE_TO_MANY' | 'ONE_TO_ONE' | 'MANY_TO_MANY'

export interface SampleRelationDefinition {
  fromModelName: string
  fromColumnName: string
  toModelName: string
  toColumnName: string
  type: SampleRelationType
  description?: string
}

export const SAMPLE_RELATIONS: Record<string, SampleRelationDefinition[]> = {
  hr: [
    {
      fromModelName: 'employees',
      fromColumnName: 'emp_no',
      toModelName: 'titles',
      toColumnName: 'emp_no',
      type: 'ONE_TO_MANY',
      description: 'Each employee can hold multiple titles over time.',
    },
    {
      fromModelName: 'departments',
      fromColumnName: 'dept_no',
      toModelName: 'dept_emp',
      toColumnName: 'dept_no',
      type: 'ONE_TO_MANY',
    },
    {
      fromModelName: 'employees',
      fromColumnName: 'emp_no',
      toModelName: 'salaries',
      toColumnName: 'emp_no',
      type: 'ONE_TO_MANY',
      description: 'Each employee can have multiple salary records over time.',
    },
    {
      fromModelName: 'dept_manager',
      fromColumnName: 'emp_no',
      toModelName: 'employees',
      toColumnName: 'emp_no',
      type: 'MANY_TO_ONE',
      description: 'Department manager assignments link each manager record to one employee.',
    },
    {
      fromModelName: 'dept_emp',
      fromColumnName: 'emp_no',
      toModelName: 'employees',
      toColumnName: 'emp_no',
      type: 'MANY_TO_ONE',
      description: 'Department assignments link each assignment record to one employee.',
    },
    {
      fromModelName: 'departments',
      fromColumnName: 'dept_no',
      toModelName: 'dept_manager',
      toColumnName: 'dept_no',
      type: 'ONE_TO_MANY',
    },
  ],
  music: [
    {
      fromModelName: 'album',
      fromColumnName: 'ArtistId',
      toModelName: 'artist',
      toColumnName: 'ArtistId',
      type: 'MANY_TO_ONE',
    },
    {
      fromModelName: 'customer',
      fromColumnName: 'CustomerId',
      toModelName: 'invoice',
      toColumnName: 'CustomerId',
      type: 'ONE_TO_MANY',
    },
    {
      fromModelName: 'genre',
      fromColumnName: 'GenreId',
      toModelName: 'track',
      toColumnName: 'GenreId',
      type: 'ONE_TO_MANY',
    },
    {
      fromModelName: 'invoice',
      fromColumnName: 'InvoiceId',
      toModelName: 'invoiceLine',
      toColumnName: 'InvoiceId',
      type: 'ONE_TO_MANY',
    },
    {
      fromModelName: 'track',
      fromColumnName: 'TrackId',
      toModelName: 'invoiceLine',
      toColumnName: 'TrackId',
      type: 'ONE_TO_MANY',
    },
    {
      fromModelName: 'album',
      fromColumnName: 'AlbumId',
      toModelName: 'track',
      toColumnName: 'AlbumId',
      type: 'ONE_TO_MANY',
    },
  ],
  ecommerce: [
    {
      fromModelName: 'olist_orders_dataset',
      fromColumnName: 'customer_id',
      toModelName: 'olist_customers_dataset',
      toColumnName: 'customer_id',
      type: 'MANY_TO_ONE',
    },
    {
      fromModelName: 'olist_orders_dataset',
      fromColumnName: 'order_id',
      toModelName: 'olist_order_items_dataset',
      toColumnName: 'order_id',
      type: 'ONE_TO_MANY',
    },
    {
      fromModelName: 'olist_orders_dataset',
      fromColumnName: 'order_id',
      toModelName: 'olist_order_reviews_dataset',
      toColumnName: 'order_id',
      type: 'ONE_TO_MANY',
    },
    {
      fromModelName: 'olist_orders_dataset',
      fromColumnName: 'order_id',
      toModelName: 'olist_order_payments_dataset',
      toColumnName: 'order_id',
      type: 'ONE_TO_MANY',
    },
    {
      fromModelName: 'olist_order_items_dataset',
      fromColumnName: 'product_id',
      toModelName: 'olist_products_dataset',
      toColumnName: 'product_id',
      type: 'MANY_TO_ONE',
    },
    {
      fromModelName: 'olist_order_items_dataset',
      fromColumnName: 'seller_id',
      toModelName: 'olist_sellers_dataset',
      toColumnName: 'seller_id',
      type: 'MANY_TO_ONE',
    },
    {
      fromModelName: 'olist_geolocation_dataset',
      fromColumnName: 'geolocation_zip_code_prefix',
      toModelName: 'olist_customers_dataset',
      toColumnName: 'customer_zip_code_prefix',
      type: 'ONE_TO_MANY',
    },
    {
      fromModelName: 'olist_geolocation_dataset',
      fromColumnName: 'geolocation_zip_code_prefix',
      toModelName: 'olist_sellers_dataset',
      toColumnName: 'seller_zip_code_prefix',
      type: 'ONE_TO_MANY',
    },
    {
      fromModelName: 'product_category_name_translation',
      fromColumnName: 'product_category_name',
      toModelName: 'olist_products_dataset',
      toColumnName: 'product_category_name',
      type: 'ONE_TO_MANY',
    },
  ],
  nba: [
    {
      fromModelName: 'game',
      fromColumnName: 'Id',
      toModelName: 'line_score',
      toColumnName: 'GameId',
      type: 'ONE_TO_MANY',
    },
    {
      fromModelName: 'line_score',
      fromColumnName: 'GameId',
      toModelName: 'player_games',
      toColumnName: 'GameID',
      type: 'ONE_TO_MANY',
    },
    {
      fromModelName: 'player',
      fromColumnName: 'TeamId',
      toModelName: 'team',
      toColumnName: 'Id',
      type: 'ONE_TO_ONE',
    },
    {
      fromModelName: 'team',
      fromColumnName: 'Id',
      toModelName: 'game',
      toColumnName: 'TeamIdHome',
      type: 'ONE_TO_MANY',
    },
  ],
}
