export interface DataSourceField {
  name: string
  label: string
  type: 'text' | 'password' | 'number' | 'ssl' | 'textarea' | 'file' | 'section-title'
  placeholder?: string
  required?: boolean
  defaultValue?: string | number | boolean
  helpText?: string
}

export interface DataSourceConfig {
  key: string
  displayName: string
  icon: string
  defaultPort?: string
  fields: DataSourceField[]
  propertiesMapping: (values: Record<string, unknown>) => Record<string, unknown>
}

function defaultMapping(values: Record<string, unknown>) {
  const allowed = ['host', 'port', 'user', 'password', 'database', 'ssl', 'displayName', 'username', 'dsn']
  const props: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(values)) {
    if (allowed.includes(k) && v !== '' && v !== undefined) {
      props[k] = v
    }
  }
  return props
}

export const DATASOURCE_CONFIGS: Record<string, DataSourceConfig> = {
  postgresql: {
    key: 'postgresql',
    displayName: 'PostgreSQL',
    icon: '/images/datasource/postgreSql.svg',
    defaultPort: '5432',
    fields: [
      { name: 'displayName', label: 'Display Name', type: 'text', required: true, placeholder: 'My PostgreSQL' },
      { name: 'host', label: 'Host', type: 'text', required: true, placeholder: '10.1.1.1' },
      { name: 'port', label: 'Port', type: 'text', required: true, placeholder: '5432' },
      { name: 'user', label: 'Username', type: 'text', required: true, placeholder: 'postgres' },
      { name: 'password', label: 'Password', type: 'password', required: true },
      { name: 'database', label: 'Database Name', type: 'text', required: true, placeholder: 'mydb' },
      { name: 'ssl', label: 'Use SSL', type: 'ssl' },
    ],
    propertiesMapping: defaultMapping,
  },
  mysql: {
    key: 'mysql',
    displayName: 'MySQL',
    icon: '/images/datasource/mysql.svg',
    defaultPort: '3306',
    fields: [
      { name: 'displayName', label: 'Display Name', type: 'text', required: true, placeholder: 'My MySQL' },
      { name: 'host', label: 'Host', type: 'text', required: true, placeholder: '10.1.1.1' },
      { name: 'port', label: 'Port', type: 'text', required: true, placeholder: '3306' },
      { name: 'user', label: 'Username', type: 'text', required: true, placeholder: 'root' },
      { name: 'password', label: 'Password', type: 'password', required: false },
      { name: 'database', label: 'Database Name', type: 'text', required: true, placeholder: 'mydb' },
      { name: 'ssl', label: 'Use SSL', type: 'ssl' },
    ],
    propertiesMapping: defaultMapping,
  },
  bigquery: {
    key: 'bigquery',
    displayName: 'BigQuery',
    icon: '/images/datasource/bigQuery.svg',
    fields: [
      { name: 'displayName', label: 'Display Name', type: 'text', required: true, placeholder: 'Our BigQuery' },
      { name: 'projectId', label: 'Project ID', type: 'text', required: true, placeholder: 'The GCP project ID' },
      { name: 'datasetId', label: 'Dataset ID', type: 'text', required: true, placeholder: 'The dataset ID' },
      { name: 'credentials', label: 'Credentials', type: 'file', required: true, helpText: 'Upload your GCP service account JSON key file' },
    ],
    propertiesMapping: (values) => {
      const props: Record<string, unknown> = {}
      if (values.projectId) props.project_id = values.projectId
      if (values.datasetId) props.dataset_id = values.datasetId
      if (values.credentials) props.credentials = values.credentials
      return props
    },
  },
  duckdb: {
    key: 'duckdb',
    displayName: 'DuckDB',
    icon: '/images/datasource/duckDb.svg',
    fields: [
      { name: 'displayName', label: 'Display Name', type: 'text', required: true, placeholder: 'DuckDB' },
      { name: 'dbname', label: 'Database Name', type: 'text', required: true, placeholder: 'my_database', helpText: 'Unique identifier for this DuckDB instance' },
      { name: 'initSql', label: 'Initial SQL Statements', type: 'textarea', required: true, placeholder: "CREATE TABLE new_tbl AS SELECT * FROM read_csv('input.csv');", helpText: 'These statements are meant to be executed only once during initialization.' },
    ],
    propertiesMapping: (values) => {
      const props: Record<string, unknown> = {}
      if (values.dbname) props.dbname = values.dbname
      if (values.initSql) props.initSql = values.initSql
      if (values.configurations && Array.isArray(values.configurations)) {
        const config: Record<string, string> = {}
        for (const item of values.configurations) {
          if (item && (item as any).key && (item as any).value) {
            config[(item as any).key] = (item as any).value
          }
        }
        if (Object.keys(config).length > 0) props.configurations = config
      }
      if (values.extensions && Array.isArray(values.extensions)) {
        props.extensions = (values.extensions as string[]).filter(Boolean)
      }
      return props
    },
  },
  oracle: {
    key: 'oracle',
    displayName: 'Oracle',
    icon: '/images/datasource/oracle.svg',
    defaultPort: '1521',
    fields: [
      { name: 'displayName', label: 'Display Name', type: 'text', required: true, placeholder: 'My Oracle' },
      { name: 'host', label: 'Host', type: 'text', required: false, placeholder: '10.1.1.1' },
      { name: 'port', label: 'Port', type: 'text', required: false, placeholder: '1521' },
      { name: 'user', label: 'Username', type: 'text', required: true, placeholder: 'system' },
      { name: 'password', label: 'Password', type: 'password', required: true },
      { name: 'database', label: 'Database Name / SID', type: 'text', required: false, placeholder: 'ORCL' },
      { name: 'dsn', label: 'DSN', type: 'textarea', required: false, placeholder: '(DESCRIPTION=(ADDRESS=(PROTOCOL=TCP)(HOST=host)(PORT=port))(CONNECT_DATA=(SERVICE_NAME=service)))', helpText: 'Oracle Data Source Name - alternative to host/port/database' },
    ],
    propertiesMapping: defaultMapping,
  },
  mssql: {
    key: 'mssql',
    displayName: 'SQL Server',
    icon: '/images/datasource/sqlserver.svg',
    defaultPort: '1433',
    fields: [
      { name: 'displayName', label: 'Display Name', type: 'text', required: true, placeholder: 'My SQL Server' },
      { name: 'host', label: 'Host', type: 'text', required: true, placeholder: '10.1.1.1' },
      { name: 'port', label: 'Port', type: 'text', required: true, placeholder: '1433' },
      { name: 'user', label: 'Username', type: 'text', required: true, placeholder: 'sa' },
      { name: 'password', label: 'Password', type: 'password', required: true },
      { name: 'database', label: 'Database Name', type: 'text', required: true, placeholder: 'mydb' },
      { name: 'ssl', label: 'Enable Trust Server Certificate', type: 'ssl', defaultValue: true, helpText: 'Skip server certificate validation' },
    ],
    propertiesMapping: defaultMapping,
  },
  clickhouse: {
    key: 'clickhouse',
    displayName: 'ClickHouse',
    icon: '/images/datasource/clickhouse.svg',
    defaultPort: '8443',
    fields: [
      { name: 'displayName', label: 'Display Name', type: 'text', required: true, placeholder: 'My ClickHouse' },
      { name: 'host', label: 'Host', type: 'text', required: true, placeholder: '<account>.clickhouse.cloud' },
      { name: 'port', label: 'Port', type: 'text', required: true, placeholder: '8443' },
      { name: 'user', label: 'Username', type: 'text', required: true, placeholder: 'default' },
      { name: 'password', label: 'Password', type: 'password', required: true },
      { name: 'database', label: 'Database Name', type: 'text', required: true, placeholder: 'default' },
      { name: 'ssl', label: 'Use SSL', type: 'ssl' },
    ],
    propertiesMapping: defaultMapping,
  },
  trino: {
    key: 'trino',
    displayName: 'Trino',
    icon: '/images/datasource/trino.svg',
    defaultPort: '8080',
    fields: [
      { name: 'displayName', label: 'Display Name', type: 'text', required: true, placeholder: 'My Trino' },
      { name: 'host', label: 'Host', type: 'text', required: true, placeholder: '10.1.1.1' },
      { name: 'port', label: 'Port', type: 'text', required: true, placeholder: '8080' },
      { name: 'schemas', label: 'Schemas', type: 'text', required: true, placeholder: 'catalog.schema1, catalog.schema2', helpText: 'Comma-separated catalog.schema pairs' },
      { name: 'username', label: 'Username', type: 'text', required: true, placeholder: 'admin' },
      { name: 'password', label: 'Password', type: 'password', required: false },
      { name: 'ssl', label: 'Use SSL', type: 'ssl' },
    ],
    propertiesMapping: (values) => {
      const props: Record<string, unknown> = { ...defaultMapping(values) }
      if (values.username) props.username = values.username
      if (values.schemas) props.schemas = values.schemas
      return props
    },
  },
  snowflake: {
    key: 'snowflake',
    displayName: 'Snowflake',
    icon: '/images/datasource/snowflake.svg',
    fields: [
      { name: 'displayName', label: 'Display Name', type: 'text', required: true, placeholder: 'My Snowflake' },
      { name: 'account', label: 'Account', type: 'text', required: true, placeholder: '<org_id>-<user_id>' },
      { name: 'database', label: 'Database Name', type: 'text', required: true, placeholder: 'SNOWFLAKE_SAMPLE_DATA' },
      { name: 'schema', label: 'Schema', type: 'text', required: true, placeholder: 'PUBLIC' },
      { name: 'warehouse', label: 'Warehouse (optional)', type: 'text', required: false, placeholder: 'COMPUTE_WH' },
      { name: 'user', label: 'User', type: 'text', required: true, placeholder: 'snowflake_user' },
      { name: 'password', label: 'Password', type: 'password', required: true, helpText: 'Username and password authentication will be deprecated by November 2025. We recommend switching to key pair authentication.' },
    ],
    propertiesMapping: (values) => {
      const props: Record<string, unknown> = {}
      if (values.account) props.account = values.account
      if (values.database) props.database = values.database
      if (values.schema) props.schema = values.schema
      if (values.warehouse) props.warehouse = values.warehouse
      if (values.user) props.user = values.user
      if (values.password) props.password = values.password
      if (values.privateKey) props.privateKey = values.privateKey
      return props
    },
  },
  athena: {
    key: 'athena',
    displayName: 'Athena (Trino)',
    icon: '/images/datasource/athena.svg',
    fields: [
      { name: 'displayName', label: 'Display Name', type: 'text', required: true, placeholder: 'My Athena' },
      { name: 'schema', label: 'Database (Schema)', type: 'text', required: true, placeholder: 'The Athena database that contains your tables' },
      { name: 's3StagingDir', label: 'S3 Staging Directory', type: 'text', required: true, placeholder: 's3://bucket/path', helpText: 'The S3 path where Athena stores query results' },
      { name: 'awsRegion', label: 'AWS Region', type: 'text', required: true, placeholder: 'us-east-1' },
    ],
    propertiesMapping: (values) => {
      const props: Record<string, unknown> = {}
      if (values.schema) props.schema = values.schema
      if (values.s3StagingDir) props.s3_staging_dir = values.s3StagingDir
      if (values.awsRegion) props.aws_region = values.awsRegion
      if (values.athenaAuthType) props.athena_auth_type = values.athenaAuthType
      if (values.awsAccessKey) props.aws_access_key = values.awsAccessKey
      if (values.awsSecretKey) props.aws_secret_key = values.awsSecretKey
      if (values.webIdentityToken) props.web_identity_token = values.webIdentityToken
      if (values.roleArn) props.role_arn = values.roleArn
      if (values.roleSessionName) props.role_session_name = values.roleSessionName
      return props
    },
  },
  redshift: {
    key: 'redshift',
    displayName: 'Redshift',
    icon: '/images/datasource/redshift.svg',
    defaultPort: '5439',
    fields: [
      { name: 'displayName', label: 'Display Name', type: 'text', required: true, placeholder: 'My Redshift' },
      { name: 'host', label: 'Host', type: 'text', required: false, placeholder: 'cluster.xxxxx.us-east-2.redshift.amazonaws.com' },
      { name: 'port', label: 'Port', type: 'text', required: false, placeholder: '5439' },
      { name: 'user', label: 'Username', type: 'text', required: true, placeholder: 'admin' },
      { name: 'password', label: 'Password', type: 'password', required: false },
      { name: 'database', label: 'Database Name', type: 'text', required: true, placeholder: 'dev' },
    ],
    propertiesMapping: (values) => {
      const props: Record<string, unknown> = { ...defaultMapping(values) }
      if (values.redshiftType) props.redshift_type = values.redshiftType
      if (values.clusterIdentifier) props.cluster_identifier = values.clusterIdentifier
      if (values.awsRegion) props.aws_region = values.awsRegion
      if (values.awsAccessKey) props.aws_access_key = values.awsAccessKey
      if (values.awsSecretKey) props.aws_secret_key = values.awsSecretKey
      return props
    },
  },
  databricks: {
    key: 'databricks',
    displayName: 'Databricks',
    icon: '/images/datasource/databricks.svg',
    fields: [
      { name: 'displayName', label: 'Display Name', type: 'text', required: true, placeholder: 'My Databricks' },
      { name: 'serverHostname', label: 'Server Hostname', type: 'text', required: true, placeholder: 'adb-123456789.12.azuredatabricks.net' },
      { name: 'httpPath', label: 'HTTP Path', type: 'text', required: true, placeholder: '/sql/1.0/endpoints/abc123' },
      { name: 'accessToken', label: 'Access Token', type: 'password', required: true, placeholder: 'Enter your Databricks personal access token' },
    ],
    propertiesMapping: (values) => {
      const props: Record<string, unknown> = {}
      if (values.serverHostname) props.server_hostname = values.serverHostname
      if (values.httpPath) props.http_path = values.httpPath
      if (values.databricksType) props.databricks_type = values.databricksType
      if (values.accessToken) props.access_token = values.accessToken
      if (values.clientId) props.client_id = values.clientId
      if (values.clientSecret) props.client_secret = values.clientSecret
      if (values.azureTenantId) props.azure_tenant_id = values.azureTenantId
      return props
    },
  },
}

export const DATASOURCE_TYPES = Object.keys(DATASOURCE_CONFIGS)

export function getDatasourceConfig(key: string): DataSourceConfig | undefined {
  return DATASOURCE_CONFIGS[key]
}

export function getDatasourceOptions() {
  return DATASOURCE_TYPES.map((key) => ({
    key,
    ...DATASOURCE_CONFIGS[key],
  }))
}
