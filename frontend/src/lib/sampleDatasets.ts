export interface SampleTable {
  tableName: string
  filePath: string
  isParquet: boolean
  primaryKey?: string
  displayName?: string
  description?: string
  columns?: { name: string; type: string; displayName?: string; description?: string | null }[]
}

export interface SampleDataset {
  key: string
  displayName: string
  description: string
  tables: SampleTable[]
  tableCount: number
}

const buildInitSql = (dataset: SampleDataset): string => {
  return dataset.tables
    .map((table) => {
      const { tableName, filePath, isParquet, columns } = table
      if (isParquet) {
        return `CREATE TABLE ${tableName} AS SELECT * FROM read_parquet('${filePath}');`
      }
      const schema = columns
        ?.map((c) => `'${c.name}': '${c.type}'`)
        .join(', ')
      return `CREATE TABLE ${tableName} AS SELECT * FROM read_csv('${filePath}', header=true${
        schema ? `, columns={${schema}}` : ''
      });`
    })
    .join('\n')
}

export const SAMPLE_DATASETS: Record<string, SampleDataset> = {
  ecommerce: {
    key: 'ecommerce',
    displayName: 'E-commerce',
    description: 'Brazilian e-commerce dataset with 9 tables covering customers, orders, payments, products, reviews, sellers, and geolocation.',
    tableCount: 9,
    tables: [
      {
        tableName: 'olist_customers_dataset',
        primaryKey: 'customer_id',
        displayName: 'customers',
        description: 'Customer profile and location data for Brazilian e-commerce orders.',
        filePath: 'https://assets.getwren.ai/sample_data/brazilian-ecommerce/olist_customers_dataset.parquet',
        isParquet: true,
        columns: [
          { name: 'customer_city', type: 'VARCHAR', description: 'Name of the city where the customer is located' },
          { name: 'customer_id', type: 'VARCHAR', description: 'Unique customer identifier used on orders.' },
          { name: 'customer_state', type: 'VARCHAR', description: 'Name of the state where the customer is located' },
          { name: 'customer_unique_id', type: 'VARCHAR', description: 'Unique id of the customer' },
          { name: 'customer_zip_code_prefix', type: 'VARCHAR', description: 'First 5 digits of customer zip code' },
        ],
      },
      {
        tableName: 'olist_order_items_dataset',
        primaryKey: 'order_item_id',
        displayName: 'order items',
        description: 'Order item lines with products, sellers, prices, and shipping costs.',
        filePath: 'https://assets.getwren.ai/sample_data/brazilian-ecommerce/olist_order_items_dataset.parquet',
        isParquet: true,
        columns: [
          { name: 'freight_value', type: 'DOUBLE', description: 'Cost of shipping associated with the specific order item' },
          { name: 'order_id', type: 'VARCHAR', description: 'Unique identifier for the order across the platform' },
          { name: 'order_item_id', type: 'BIGINT', description: 'Unique identifier for each item within a specific order' },
          { name: 'price', type: 'DOUBLE', description: 'Price of the individual item within the order' },
          { name: 'product_id', type: 'VARCHAR', description: 'Unique identifier for the product sold in the order.' },
          { name: 'seller_id', type: 'VARCHAR', description: 'Unique identifier of the seller who fulfilled the order item.' },
          { name: 'shipping_limit_date', type: 'TIMESTAMP', description: 'Deadline for the order item to be shipped by the seller.' },
        ],
      },
      {
        tableName: 'olist_orders_dataset',
        primaryKey: 'order_id',
        displayName: 'orders',
        description: 'Customer orders with lifecycle timestamps, status, and customer identifiers.',
        filePath: 'https://assets.getwren.ai/sample_data/brazilian-ecommerce/olist_orders_dataset.parquet',
        isParquet: true,
        columns: [
          { name: 'customer_id', type: 'VARCHAR', description: 'Unique identifier for the customer who placed the order.' },
          { name: 'order_approved_at', type: 'TIMESTAMP', description: 'Date and time when the order was approved for processing.' },
          { name: 'order_delivered_carrier_date', type: 'TIMESTAMP', description: 'Date when the order was handed over to the carrier.' },
          { name: 'order_delivered_customer_date', type: 'TIMESTAMP', description: 'Date when the order was delivered to the customer.' },
          { name: 'order_estimated_delivery_date', type: 'TIMESTAMP', description: 'Expected delivery date based on the initial estimate.' },
          { name: 'order_id', type: 'VARCHAR', description: 'Unique identifier for the specific order' },
          { name: 'order_purchase_timestamp', type: 'TIMESTAMP', description: 'Date and time when the order was placed by the customer.' },
          { name: 'order_status', type: 'VARCHAR', description: 'Current status of the order.' },
        ],
      },
      {
        tableName: 'olist_order_payments_dataset',
        primaryKey: 'order_id',
        displayName: 'order payments',
        description: 'Payment details for each order, including payment methods, amounts, installments, and sequences.',
        filePath: 'https://assets.getwren.ai/sample_data/brazilian-ecommerce/olist_order_payments_dataset.parquet',
        isParquet: true,
        columns: [
          { name: 'order_id', type: 'VARCHAR', description: 'Unique identifier for the order associated with the payment.' },
          { name: 'payment_installments', type: 'BIGINT', description: 'Number of installments the payment is divided into for the order.' },
          { name: 'payment_sequential', type: 'BIGINT', description: 'Sequence number for multiple payments within the same order.' },
          { name: 'payment_type', type: 'VARCHAR', description: 'Method used for the payment, such as credit card, debit, or voucher.' },
          { name: 'payment_value', type: 'DOUBLE', description: 'Total amount paid in the specific transaction.' },
        ],
      },
      {
        tableName: 'olist_products_dataset',
        primaryKey: 'product_id',
        displayName: 'products',
        description: 'Product catalog details including categories, dimensions, weight, description length, and photos.',
        filePath: 'https://assets.getwren.ai/sample_data/brazilian-ecommerce/olist_products_dataset.parquet',
        isParquet: true,
        columns: [
          { name: 'product_category_name', type: 'VARCHAR', description: 'Name of the product category to which the item belongs.' },
          { name: 'product_description_lenght', type: 'BIGINT', description: 'Length of the product description in characters.' },
          { name: 'product_height_cm', type: 'BIGINT', description: 'Height of the product in centimeters.' },
          { name: 'product_id', type: 'VARCHAR', description: 'Unique identifier for the product' },
          { name: 'product_length_cm', type: 'BIGINT', description: 'Length of the product in centimeters' },
          { name: 'product_name_lenght', type: 'BIGINT', description: 'Length of the product name in characters' },
          { name: 'product_photos_qty', type: 'BIGINT', description: 'Number of photos available for the product' },
          { name: 'product_weight_g', type: 'BIGINT', description: 'Weight of the product in grams' },
          { name: 'product_width_cm', type: 'BIGINT', description: 'Width of the product in centimeters' },
        ],
      },
      {
        tableName: 'olist_order_reviews_dataset',
        primaryKey: 'review_id',
        displayName: 'order reviews',
        description: 'Customer reviews for each order, including comments, ratings, and review timestamps.',
        filePath: 'https://assets.getwren.ai/sample_data/brazilian-ecommerce/olist_order_reviews_dataset.parquet',
        isParquet: true,
        columns: [
          { name: 'order_id', type: 'VARCHAR', description: 'Unique identifier linking the review to the corresponding order.' },
          { name: 'review_answer_timestamp', type: 'TIMESTAMP', description: 'Date and time when the review was responded to by the seller' },
          { name: 'review_comment_message', type: 'VARCHAR', description: 'Detailed feedback or comments provided by the customer regarding the order.' },
          { name: 'review_comment_title', type: 'VARCHAR', description: "Summary or title of the customer's review" },
          { name: 'review_creation_date', type: 'TIMESTAMP', description: 'Date and time when the customer initially submitted the review.' },
          { name: 'review_id', type: 'VARCHAR', description: 'Unique identifier for the specific review entry.' },
          { name: 'review_score', type: 'BIGINT', description: 'Numeric rating given by the customer, typically ranging from 1 to 5.' },
        ],
      },
      {
        tableName: 'olist_geolocation_dataset',
        displayName: 'geolocation',
        description: 'Brazilian zip code geolocation data with latitude, longitude, city, and state.',
        filePath: 'https://assets.getwren.ai/sample_data/brazilian-ecommerce/olist_geolocation_dataset.parquet',
        isParquet: true,
        columns: [
          { name: 'geolocation_city', type: 'VARCHAR', description: 'The city name of the geolocation' },
          { name: 'geolocation_lat', type: 'DOUBLE', description: 'The coordinates for the location latitude' },
          { name: 'geolocation_lng', type: 'DOUBLE', description: 'The coordinates for the location longitude' },
          { name: 'geolocation_state', type: 'VARCHAR', description: 'The state of the geolocation' },
          { name: 'geolocation_zip_code_prefix', type: 'VARCHAR', description: 'First 5 digits of zip code' },
        ],
      },
      {
        tableName: 'olist_sellers_dataset',
        displayName: 'sellers',
        description: 'Seller profile and location data for sellers that fulfilled orders.',
        filePath: 'https://assets.getwren.ai/sample_data/brazilian-ecommerce/olist_sellers_dataset.parquet',
        isParquet: true,
        columns: [
          { name: 'seller_city', type: 'VARCHAR', description: 'The Brazilian city where the seller is located' },
          { name: 'seller_id', type: 'VARCHAR', description: 'Unique identifier for the seller on the platform' },
          { name: 'seller_state', type: 'VARCHAR', description: 'The Brazilian state where the seller is located' },
          { name: 'seller_zip_code_prefix', type: 'VARCHAR', description: 'First 5 digits of seller zip code' },
        ],
      },
      {
        tableName: 'product_category_name_translation',
        primaryKey: 'product_category_name',
        displayName: 'product category name translation',
        description: 'Translations of product categories from Portuguese to English.',
        filePath: 'https://assets.getwren.ai/sample_data/brazilian-ecommerce/product_category_name_translation.parquet',
        isParquet: true,
        columns: [
          { name: 'product_category_name', type: 'VARCHAR', description: 'Original name of the product category in Portuguese.' },
          { name: 'product_category_name_english', type: 'VARCHAR', description: 'Translated name of the product category in English.' },
        ],
      },
    ],
  },
  hr: {
    key: 'hr',
    displayName: 'Human Resource',
    description: 'Employee management dataset with 6 tables covering salaries, titles, departments, employees, and managers.',
    tableCount: 6,
    tables: [
      {
        tableName: 'salaries',
        description: 'Tracks the salary of employees, including the period during which each salary was valid.',
        displayName: 'salaries',
        filePath: 'https://assets.getwren.ai/sample_data/employees/salaries.parquet',
        isParquet: true,
        columns: [
          { name: 'emp_no', type: 'INTEGER', description: 'The employee number' },
          { name: 'salary', type: 'INTEGER', description: 'The salary of the employee.' },
          { name: 'from_date', type: 'DATE', description: 'The start date of the salary period.' },
          { name: 'to_date', type: 'DATE', description: 'The end date of the salary period.' },
        ],
      },
      {
        tableName: 'titles',
        description: 'Tracks the titles or positions held by employees, including the period during which they held each title.',
        displayName: 'titles',
        filePath: 'https://assets.getwren.ai/sample_data/employees/titles.parquet',
        isParquet: true,
        columns: [
          { name: 'emp_no', type: 'INTEGER', description: 'The employee number' },
          { name: 'title', type: 'VARCHAR', description: 'The title or position held by the employee.' },
          { name: 'from_date', type: 'DATE', description: 'The start date when the employee held this title' },
          { name: 'to_date', type: 'DATE', description: 'The end date when the employee held this title. This can be NULL if the employee currently holds the title.' },
        ],
      },
      {
        tableName: 'dept_emp',
        description: 'Tracks employee assignments to departments over time.',
        displayName: 'dept_emp',
        filePath: 'https://assets.getwren.ai/sample_data/employees/dept_emp.parquet',
        isParquet: true,
        columns: [
          { name: 'emp_no', type: 'INTEGER', description: 'The employee number.' },
          { name: 'dept_no', type: 'VARCHAR', description: 'The department number the employee is associated with.' },
          { name: 'from_date', type: 'DATE', description: "The start date of the employee's association with the department." },
          { name: 'to_date', type: 'DATE', description: "The end date of the employee's association with the department." },
        ],
      },
      {
        tableName: 'departments',
        description: 'Stores department identifiers and names.',
        displayName: 'departments',
        filePath: 'https://assets.getwren.ai/sample_data/employees/departments.parquet',
        isParquet: true,
        columns: [
          { name: 'dept_name', type: 'VARCHAR', description: 'The name of the department.' },
          { name: 'dept_no', type: 'VARCHAR', description: 'A unique identifier for each department. It serves as the primary key of the table.' },
        ],
      },
      {
        tableName: 'employees',
        description: 'Stores basic information about employees such as employee number, name, gender, birth date, and hire date.',
        displayName: 'employees',
        filePath: 'https://assets.getwren.ai/sample_data/employees/employees.parquet',
        isParquet: true,
        columns: [
          { name: 'birth_date', type: 'DATE', description: 'The birth date of the employee.' },
          { name: 'first_name', type: 'VARCHAR', description: 'The first name of the employee.' },
          { name: 'last_name', type: 'VARCHAR', description: 'The last name of the employee.' },
          { name: 'gender', type: 'VARCHAR', description: "The gender of the employee, with possible values 'M' or 'F'." },
          { name: 'hire_date', type: 'DATE', description: 'The date when the employee was hired.' },
          { name: 'emp_no', type: 'INTEGER', description: 'A unique identifier for each employee. It serves as the primary key of the table.' },
        ],
      },
      {
        tableName: 'dept_manager',
        description: 'Tracks the assignment of managers to departments, including the period during which they managed a department.',
        displayName: 'dept_manager',
        filePath: 'https://assets.getwren.ai/sample_data/employees/dept_manager.parquet',
        isParquet: true,
        columns: [
          { name: 'from_date', type: 'DATE', description: 'The start date of the employee managerial role in the department.' },
          { name: 'to_date', type: 'DATE', description: 'The end date of the employee managerial role in the department.' },
          { name: 'emp_no', type: 'INTEGER', description: 'The employee number of the department manager.' },
          { name: 'dept_no', type: 'VARCHAR', description: 'The department number that the manager is assigned to.' },
        ],
      },
    ],
  },
  music: {
    key: 'music',
    displayName: 'Music',
    description: 'Music store dataset with 7 tables covering albums, artists, customers, invoices, and tracks.',
    tableCount: 7,
    tables: [
      {
        tableName: 'album',
        filePath: 'https://wrenai-public.s3.amazonaws.com/demo/Music/Album.csv',
        isParquet: false,
        columns: [
          { name: 'AlbumId', type: 'INT' },
          { name: 'Title', type: 'varchar' },
          { name: 'ArtistId', type: 'INT' },
        ],
      },
      {
        tableName: 'artist',
        filePath: 'https://wrenai-public.s3.amazonaws.com/demo/Music/Artist.csv',
        isParquet: false,
        columns: [
          { name: 'ArtistId', type: 'INT' },
          { name: 'Name', type: 'varchar' },
        ],
      },
      {
        tableName: 'customer',
        filePath: 'https://wrenai-public.s3.amazonaws.com/demo/Music/Customer.csv',
        isParquet: false,
        columns: [
          { name: 'CustomerId', type: 'BIGINT' },
          { name: 'FirstName', type: 'VARCHAR' },
          { name: 'LastName', type: 'VARCHAR' },
          { name: 'Company', type: 'VARCHAR' },
          { name: 'Address', type: 'VARCHAR' },
          { name: 'City', type: 'VARCHAR' },
          { name: 'State', type: 'VARCHAR' },
          { name: 'Country', type: 'VARCHAR' },
          { name: 'PostalCode', type: 'VARCHAR' },
          { name: 'Phone', type: 'VARCHAR' },
          { name: 'Fax', type: 'VARCHAR' },
          { name: 'Email', type: 'VARCHAR' },
          { name: 'SupportRepId', type: 'BIGINT' },
        ],
      },
      {
        tableName: 'genre',
        filePath: 'https://wrenai-public.s3.amazonaws.com/demo/Music/Genre.csv',
        isParquet: false,
        columns: [
          { name: 'GenreId', type: 'BIGINT' },
          { name: 'Name', type: 'VARCHAR' },
        ],
      },
      {
        tableName: 'invoice',
        filePath: 'https://wrenai-public.s3.amazonaws.com/demo/Music/Invoice.csv',
        isParquet: false,
        columns: [
          { name: 'InvoiceId', type: 'BIGINT' },
          { name: 'CustomerId', type: 'BIGINT' },
          { name: 'InvoiceDate', type: 'Date' },
          { name: 'BillingAddress', type: 'VARCHAR' },
          { name: 'BillingCity', type: 'VARCHAR' },
          { name: 'BillingState', type: 'VARCHAR' },
          { name: 'BillingCountry', type: 'VARCHAR' },
          { name: 'BillingPostalCode', type: 'VARCHAR' },
          { name: 'Total', type: 'DOUBLE' },
        ],
      },
      {
        tableName: 'invoiceLine',
        filePath: 'https://wrenai-public.s3.amazonaws.com/demo/Music/InvoiceLine.csv',
        isParquet: false,
        columns: [
          { name: 'InvoiceLineId', type: 'BIGINT' },
          { name: 'InvoiceId', type: 'BIGINT' },
          { name: 'TrackId', type: 'BIGINT' },
          { name: 'UnitPrice', type: 'DOUBLE' },
          { name: 'Quantity', type: 'BIGINT' },
        ],
      },
      {
        tableName: 'track',
        filePath: 'https://wrenai-public.s3.amazonaws.com/demo/Music/Track.csv',
        isParquet: false,
        columns: [
          { name: 'TrackId', type: 'BIGINT' },
          { name: 'Name', type: 'VARCHAR' },
          { name: 'AlbumId', type: 'BIGINT' },
          { name: 'MediaTypeId', type: 'BIGINT' },
          { name: 'GenreId', type: 'BIGINT' },
          { name: 'Composer', type: 'VARCHAR' },
          { name: 'Milliseconds', type: 'BIGINT' },
          { name: 'Bytes', type: 'BIGINT' },
          { name: 'UnitPrice', type: 'DOUBLE' },
        ],
      },
    ],
  },
  nba: {
    key: 'nba',
    displayName: 'NBA',
    description: 'NBA basketball dataset with 5 tables covering games, player statistics, line scores, teams, and players.',
    tableCount: 5,
    tables: [
      {
        tableName: 'game',
        filePath: 'https://wrenai-public.s3.amazonaws.com/demo/v0.3.0/NBA/game.csv',
        isParquet: false,
        columns: [
          { name: 'SeasonId', type: 'BIGINT' },
          { name: 'TeamIdHome', type: 'BIGINT' },
          { name: 'Id', type: 'BIGINT' },
          { name: 'GameDate', type: 'DATE' },
          { name: 'WlHome', type: 'VARCHAR' },
          { name: 'Min', type: 'BIGINT' },
          { name: 'FgmHome', type: 'BIGINT' },
          { name: 'FgaHome', type: 'BIGINT' },
          { name: 'FgPct_home', type: 'DOUBLE' },
          { name: 'threepHome', type: 'BIGINT' },
          { name: 'threepaHome', type: 'BIGINT' },
          { name: 'fg3_pct_home', type: 'DOUBLE' },
          { name: 'FtmHome', type: 'BIGINT' },
          { name: 'FtaHome', type: 'BIGINT' },
          { name: 'ft_pct_home', type: 'DOUBLE' },
          { name: 'OrebHome', type: 'BIGINT' },
          { name: 'DrebHome', type: 'BIGINT' },
          { name: 'RebHome', type: 'BIGINT' },
          { name: 'AstHome', type: 'BIGINT' },
          { name: 'StlHome', type: 'BIGINT' },
          { name: 'BlkHome', type: 'BIGINT' },
          { name: 'TovHome', type: 'BIGINT' },
          { name: 'PfHome', type: 'BIGINT' },
          { name: 'PtsHome', type: 'BIGINT' },
          { name: 'PlusMinusHome', type: 'BIGINT' },
          { name: 'TeamIdAway', type: 'BIGINT' },
          { name: 'WlAway', type: 'VARCHAR' },
          { name: 'FgmAway', type: 'BIGINT' },
          { name: 'FgaAway', type: 'BIGINT' },
          { name: 'fg_pct_away', type: 'DOUBLE' },
          { name: 'threepAway', type: 'BIGINT' },
          { name: 'threepaAway', type: 'BIGINT' },
          { name: 'Fg3_pct_away', type: 'DOUBLE' },
          { name: 'FtmAway', type: 'BIGINT' },
          { name: 'FtaAway', type: 'BIGINT' },
          { name: 'Ft_pct_away', type: 'DOUBLE' },
          { name: 'OrebAway', type: 'BIGINT' },
          { name: 'DrebAway', type: 'BIGINT' },
          { name: 'RebAway', type: 'BIGINT' },
          { name: 'AstAway', type: 'BIGINT' },
          { name: 'StlAway', type: 'BIGINT' },
          { name: 'BlkAway', type: 'BIGINT' },
          { name: 'TovAway', type: 'BIGINT' },
          { name: 'PfAway', type: 'BIGINT' },
          { name: 'PtsAway', type: 'BIGINT' },
          { name: 'PlusMinusAway', type: 'BIGINT' },
          { name: 'SeasonType', type: 'VARCHAR' },
        ],
      },
      {
        tableName: 'line_score',
        filePath: 'https://wrenai-public.s3.amazonaws.com/demo/v0.3.0/NBA/line_score.csv',
        isParquet: false,
        columns: [
          { name: 'GameDate', type: 'DATE' },
          { name: 'GameSequence', type: 'BIGINT' },
          { name: 'GameId', type: 'BIGINT' },
          { name: 'TeamIdHome', type: 'BIGINT' },
          { name: 'TeamWinsLossesHome', type: 'VARCHAR' },
          { name: 'PtsQtr1Home', type: 'BIGINT' },
          { name: 'PtsQtr2Home', type: 'BIGINT' },
          { name: 'PtsQtr3Home', type: 'BIGINT' },
          { name: 'PtsQtr4Home', type: 'BIGINT' },
          { name: 'PtsOt1Home', type: 'BIGINT' },
          { name: 'PtsHome', type: 'BIGINT' },
          { name: 'TeamIdAway', type: 'BIGINT' },
          { name: 'TeamWinsLossesAway', type: 'VARCHAR' },
          { name: 'PtsQtr1Away', type: 'BIGINT' },
          { name: 'PtsQtr2Away', type: 'BIGINT' },
          { name: 'PtsQtr3Away', type: 'BIGINT' },
          { name: 'PtsQtr4Away', type: 'BIGINT' },
          { name: 'PtsOt1Away', type: 'BIGINT' },
          { name: 'PtsAway', type: 'BIGINT' },
        ],
      },
      {
        tableName: 'player_games',
        filePath: 'https://wrenai-public.s3.amazonaws.com/demo/v0.3.0/NBA/player_game.csv',
        isParquet: false,
        columns: [
          { name: 'Id', type: 'BIGINT' },
          { name: 'PlayerID', type: 'BIGINT' },
          { name: 'GameID', type: 'BIGINT' },
          { name: 'Date', type: 'DATE' },
          { name: 'Age', type: 'VARCHAR' },
          { name: 'Tm', type: 'VARCHAR' },
          { name: 'Opp', type: 'VARCHAR' },
          { name: 'MP', type: 'VARCHAR' },
          { name: 'FG', type: 'BIGINT' },
          { name: 'FGA', type: 'BIGINT' },
          { name: 'threeP', type: 'BIGINT' },
          { name: 'threePA', type: 'BIGINT' },
          { name: 'FT', type: 'BIGINT' },
          { name: 'FTA', type: 'BIGINT' },
          { name: 'ORB', type: 'BIGINT' },
          { name: 'DRB', type: 'BIGINT' },
          { name: 'TRB', type: 'BIGINT' },
          { name: 'AST', type: 'BIGINT' },
          { name: 'STL', type: 'BIGINT' },
          { name: 'BLK', type: 'BIGINT' },
          { name: 'TOV', type: 'BIGINT' },
          { name: 'PF', type: 'BIGINT' },
          { name: 'PTS', type: 'BIGINT' },
        ],
      },
      {
        tableName: 'player',
        filePath: 'https://wrenai-public.s3.amazonaws.com/demo/v0.3.0/NBA/player.csv',
        isParquet: false,
        columns: [
          { name: 'Id', type: 'BIGINT' },
          { name: 'TeamId', type: 'BIGINT' },
          { name: 'FullName', type: 'VARCHAR' },
          { name: 'FirstName', type: 'VARCHAR' },
          { name: 'LastName', type: 'VARCHAR' },
        ],
      },
      {
        tableName: 'team',
        filePath: 'https://wrenai-public.s3.amazonaws.com/demo/v0.3.0/NBA/team.csv',
        isParquet: false,
        columns: [
          { name: 'Id', type: 'BIGINT' },
          { name: 'FullName', type: 'VARCHAR' },
          { name: 'Abbreviation', type: 'VARCHAR' },
          { name: 'Nickname', type: 'VARCHAR' },
          { name: 'City', type: 'VARCHAR' },
          { name: 'State', type: 'VARCHAR' },
          { name: 'YearFounded', type: 'INT' },
        ],
      },
    ],
  },
}

export const SAMPLE_DATASET_LIST = Object.values(SAMPLE_DATASETS)

export function getSampleTableDetails(datasetKey: string) {
  const ds = SAMPLE_DATASETS[datasetKey]
  if (!ds) return []
  return ds.tables.map((table) => ({
    name: table.tableName,
    reference: table.tableName,
    displayName: table.displayName,
    description: table.description,
    columns: (table.columns ?? []).map((column) => ({
      name: column.name,
      type: column.type,
      displayName: column.displayName,
      description: column.description,
      is_primary_key: table.primaryKey === column.name,
    })),
  }))
}

export function getInitSql(datasetKey: string): string {
  const ds = SAMPLE_DATASETS[datasetKey]
  if (!ds) return ''
  return buildInitSql(ds)
}

export function getCombinedInitSql(): string {
  return SAMPLE_DATASET_LIST.map((ds) => buildInitSql(ds)).join('\n\n')
}
