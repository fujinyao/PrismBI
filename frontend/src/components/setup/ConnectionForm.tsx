'use client'

import { useState, useRef, useCallback, useEffect } from 'react'
import { Input } from '@/components/ui/Input'
import { Button } from '@/components/ui/Button'
import { getDatasourceConfig, type DataSourceField } from '@/lib/datasourceConfig'
import { useI18nStore } from '@/stores/i18nStore'

interface ConnectionFormProps {
  dsType: string
  onSubmit: (values: Record<string, unknown>) => void
  loading?: boolean
  submitLabel?: string
  preserveDisplayName?: boolean
  initialValues?: Record<string, unknown>
  skipMapping?: boolean
}

function SslSwitch({
  value,
  onChange,
  label,
}: {
  value: boolean
  onChange: (v: boolean) => void
  label: string
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2">
      <button
        type="button"
        role="switch"
        aria-checked={value}
        onClick={() => onChange(!value)}
        className={`relative inline-flex h-5 w-9 shrink-0 rounded-full border-2 border-transparent transition-colors focus:outline-none focus:ring-2 focus:ring-primary-300 ${
          value ? 'bg-primary' : 'bg-gray-300 dark:bg-gray-600'
        }`}
      >
        <span
          className={`inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${
            value ? 'translate-x-4' : 'translate-x-0'
          }`}
        />
      </button>
      <span className="text-sm text-gray-700 dark:text-gray-300">{label}</span>
    </label>
  )
}

function FileUpload({
  value,
  onChange,
  accept,
  helpText,
}: {
  value?: string
  onChange: (v: string | undefined) => void
  accept?: string
  helpText?: string
}) {
  const t = useI18nStore((s) => s.t)
  const inputRef = useRef<HTMLInputElement>(null)
  const [fileName, setFileName] = useState<string>('')

  const handleFile = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0]
      if (!file) return
      setFileName(file.name)
      try {
        const text = await file.text()
        if (accept?.includes('json')) {
          const parsed = JSON.parse(text)
          onChange(JSON.stringify(parsed))
        } else {
          onChange(text)
        }
      } catch {
        onChange(undefined)
      }
    },
    [accept, onChange],
  )

  return (
    <div>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        onChange={handleFile}
        className="hidden"
      />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        className="flex items-center gap-2 rounded-md border border-gray-300 px-3 py-2 text-sm hover:bg-gray-50 dark:border-gray-600 dark:hover:bg-gray-700"
      >
        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
        </svg>
        {value ? t('setup.fileSelected', 'File selected') : t('setup.clickToUpload', 'Click to upload')}
      </button>
      {fileName && <p className="mt-1 text-xs text-gray-500">{fileName}</p>}
      {value && (
        <button
          type="button"
          onClick={() => { onChange(undefined); setFileName('') }}
          className="mt-1 text-xs text-error hover:underline"
        >
          {t('common.remove', 'Remove')}
        </button>
      )}
      {helpText && <p className="mt-1 text-xs text-gray-400">{helpText}</p>}
    </div>
  )
}

function RadioGroup({
  options,
  value,
  onChange,
  label,
}: {
  options: { label: string; value: string }[]
  value: string
  onChange: (v: string) => void
  label: string
}) {
  return (
    <div>
      <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
        {label}
      </label>
      <div className="flex flex-wrap gap-1 rounded-md border border-gray-200 p-1 dark:border-gray-600">
        {options.map((opt) => (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            className={`rounded px-3 py-1.5 text-sm transition-colors ${
              value === opt.value
                ? 'bg-primary text-white'
                : 'text-gray-600 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700'
            }`}
          >
            {opt.label}
          </button>
        ))}
      </div>
    </div>
  )
}

function KeyValueList({
  value,
  onChange,
  keyPlaceholder,
  valuePlaceholder,
}: {
  value: { key: string; value: string }[]
  onChange: (v: { key: string; value: string }[]) => void
  keyPlaceholder?: string
  valuePlaceholder?: string
}) {
  const t = useI18nStore((s) => s.t)
  const add = () => onChange([...value, { key: '', value: '' }])
  const remove = (idx: number) => onChange(value.filter((_, i) => i !== idx))
  const update = (idx: number, field: 'key' | 'value', val: string) => {
    const next: { key: string; value: string }[] = value.map((item, i) =>
      i === idx ? { ...item, [field]: val } : { ...item },
    )
    onChange(next)
  }

  return (
    <div className="space-y-2">
      {value.map((item, idx) => (
        <div key={idx} className="flex items-center gap-2">
          <Input
            placeholder={keyPlaceholder || t('setup.key', 'Key')}
            value={item.key}
            onChange={(e) => update(idx, 'key', e.target.value)}
          />
          <Input
            placeholder={valuePlaceholder || t('setup.value', 'Value')}
            value={item.value}
            onChange={(e) => update(idx, 'value', e.target.value)}
          />
          <button
            type="button"
            onClick={() => remove(idx)}
            className="shrink-0 text-gray-400 hover:text-error"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={add}
        className="flex items-center gap-1 text-sm text-primary hover:underline"
      >
        <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
        </svg>
        {t('common.add', 'Add')}
      </button>
    </div>
  )
}

function StringList({
  value,
  onChange,
  placeholder,
}: {
  value: string[]
  onChange: (v: string[]) => void
  placeholder?: string
}) {
  const t = useI18nStore((s) => s.t)
  const add = () => onChange([...value, ''])
  const remove = (idx: number) => onChange(value.filter((_, i) => i !== idx))
  const update = (idx: number, val: string) => {
    const next = [...value]
    next[idx] = val
    onChange(next)
  }

  return (
    <div className="space-y-2">
      {value.map((item, idx) => (
        <div key={idx} className="flex items-center gap-2">
          <Input
            placeholder={placeholder || t('setup.value', 'Value')}
            value={item}
            onChange={(e) => update(idx, e.target.value)}
          />
          <button
            type="button"
            onClick={() => remove(idx)}
            className="shrink-0 text-gray-400 hover:text-error"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={add}
        className="flex items-center gap-1 text-sm text-primary hover:underline"
      >
        <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
        </svg>
        {t('common.add', 'Add')}
      </button>
    </div>
  )
}
function renderField(
  field: DataSourceField,
  values: Record<string, unknown>,
  setField: (name: string, value: unknown) => void,
) {
  const value = values[field.name]
  const id = `field-${field.name}`

  switch (field.type) {
    case 'password':
      return (
        <Input
          type="password"
          placeholder={field.placeholder}
          value={(value as string) || ''}
          onChange={(e) => setField(field.name, e.target.value)}
        />
      )
    case 'textarea':
      return (
        <textarea
          className="block w-full rounded-md border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
          placeholder={field.placeholder}
          rows={4}
          value={(value as string) || ''}
          onChange={(e) => setField(field.name, e.target.value)}
        />
      )
    case 'ssl':
      return (
        <SslSwitch
          value={!!(value ?? field.defaultValue ?? false)}
          onChange={(v) => setField(field.name, v)}
          label={field.label}
        />
      )
    case 'file':
      return (
        <FileUpload
          value={value as string | undefined}
          onChange={(v) => setField(field.name, v)}
          accept=".json"
          helpText={field.helpText}
        />
      )
    default:
      return (
        <Input
          type={field.type === 'number' ? 'number' : 'text'}
          placeholder={field.placeholder}
          value={(value as string) || ''}
          onChange={(e) => setField(field.name, e.target.value)}
        />
      )
  }
}

export default function ConnectionForm({
  dsType,
  onSubmit,
  loading,
  submitLabel,
  preserveDisplayName,
  initialValues,
  skipMapping,
}: ConnectionFormProps) {
  const config = getDatasourceConfig(dsType)
  const i18nT = useI18nStore((s) => s.t)
  const [values, setValues] = useState<Record<string, unknown>>(initialValues || {})

  // DuckDB extra state
  const [configurations, setConfigurations] = useState<{ key: string; value: string }[]>([])
  const [extensions, setExtensions] = useState<string[]>([''])

  // Snowflake auth method
  const [snowflakeAuth, setSnowflakeAuth] = useState<'password' | 'keypair'>('password')
  const [privateKey, setPrivateKey] = useState<string | undefined>()

  // Athena auth method & fields
  const [athenaAuth, setAthenaAuth] = useState<string>('classic')
  const [awsAccessKey, setAwsAccessKey] = useState('')
  const [awsSecretKey, setAwsSecretKey] = useState('')
  const [webIdentityToken, setWebIdentityToken] = useState('')
  const [roleArn, setRoleArn] = useState('')
  const [roleSessionName, setRoleSessionName] = useState('')

  // Redshift auth method & fields
  const [redshiftType, setRedshiftType] = useState<string>('redshift')
  const [clusterIdentifier, setClusterIdentifier] = useState('')
  const [redshiftAwsRegion, setRedshiftAwsRegion] = useState('')
  const [redshiftAwsAccessKey, setRedshiftAwsAccessKey] = useState('')
  const [redshiftAwsSecretKey, setRedshiftAwsSecretKey] = useState('')

  // Databricks auth method
  const [databricksType, setDatabricksType] = useState<string>('token')
  const [databricksClientId, setDatabricksClientId] = useState('')
  const [databricksClientSecret, setDatabricksClientSecret] = useState('')
  const [databricksTenantId, setDatabricksTenantId] = useState('')

  // BigQuery credentials
  const [credentials, setCredentials] = useState<string | undefined>()

  useEffect(() => {
    const nextValues = initialValues || {}
    setValues(nextValues)

    if (dsType === 'duckdb') {
      const conf = Array.isArray(nextValues.configurations)
        ? (nextValues.configurations as { key: string; value: string }[])
        : []
      setConfigurations(conf)
      const ext = Array.isArray(nextValues.extensions) ? (nextValues.extensions as string[]) : ['']
      setExtensions(ext.length > 0 ? ext : [''])
    }

    if (dsType === 'snowflake') {
      const hasPrivateKey = Boolean(nextValues.privateKey)
      setSnowflakeAuth(hasPrivateKey ? 'keypair' : 'password')
      setPrivateKey((nextValues.privateKey as string | undefined) || undefined)
    }

    if (dsType === 'athena') {
      setAthenaAuth((nextValues.athenaAuthType as string) || 'classic')
      setAwsAccessKey((nextValues.awsAccessKey as string) || '')
      setAwsSecretKey((nextValues.awsSecretKey as string) || '')
      setWebIdentityToken((nextValues.webIdentityToken as string) || '')
      setRoleArn((nextValues.roleArn as string) || '')
      setRoleSessionName((nextValues.roleSessionName as string) || '')
    }

    if (dsType === 'redshift') {
      setRedshiftType((nextValues.redshiftType as string) || 'redshift')
      setClusterIdentifier((nextValues.clusterIdentifier as string) || '')
      setRedshiftAwsRegion((nextValues.awsRegion as string) || '')
      setRedshiftAwsAccessKey((nextValues.awsAccessKey as string) || '')
      setRedshiftAwsSecretKey((nextValues.awsSecretKey as string) || '')
    }

    if (dsType === 'databricks') {
      setDatabricksType((nextValues.databricksType as string) || 'token')
      setDatabricksClientId((nextValues.clientId as string) || '')
      setDatabricksClientSecret((nextValues.clientSecret as string) || '')
      setDatabricksTenantId((nextValues.azureTenantId as string) || '')
    }

    if (dsType === 'bigquery') {
      if (typeof nextValues.credentials === 'string') {
        setCredentials(nextValues.credentials)
      } else if (nextValues.credentials && typeof nextValues.credentials === 'object') {
        try {
          setCredentials(JSON.stringify(nextValues.credentials))
        } catch {
          setCredentials(undefined)
        }
      } else {
        setCredentials(undefined)
      }
    }
  }, [dsType, initialValues])

  if (!config) return null

  const setField = (name: string, value: unknown) => {
    setValues((prev) => ({ ...prev, [name]: value }))
  }

  const handleSubmit = () => {
    const displayNameRaw =
      typeof values.displayName === 'string' && values.displayName.trim()
        ? values.displayName.trim()
        : undefined

    const props: Record<string, unknown> = { ...values }

    // Remove internal/display fields unless caller wants to keep it
    if (!preserveDisplayName) {
      delete props.displayName
    }

    // Add type-specific extras
    if (dsType === 'duckdb') {
      props.configurations = configurations as any
      props.extensions = extensions as any
    }
    if (dsType === 'bigquery' && credentials) {
      try {
        props.credentials = JSON.parse(credentials)
      } catch { /* ignore */ }
    }
    if (dsType === 'snowflake') {
      if (snowflakeAuth === 'keypair' && privateKey) {
        props.privateKey = privateKey
        delete props.password
      }
    }
    if (dsType === 'athena') {
      props.athenaAuthType = athenaAuth
      if (awsAccessKey) props.awsAccessKey = awsAccessKey
      if (awsSecretKey) props.awsSecretKey = awsSecretKey
      if (webIdentityToken) props.webIdentityToken = webIdentityToken
      if (roleArn) props.roleArn = roleArn
      if (roleSessionName) props.roleSessionName = roleSessionName
    }
    if (dsType === 'redshift') {
      props.redshiftType = redshiftType
      if (clusterIdentifier) props.clusterIdentifier = clusterIdentifier
      if (redshiftAwsRegion) props.awsRegion = redshiftAwsRegion
      if (redshiftAwsAccessKey) props.awsAccessKey = redshiftAwsAccessKey
      if (redshiftAwsSecretKey) props.awsSecretKey = redshiftAwsSecretKey
    }
    if (dsType === 'databricks') {
      props.databricksType = databricksType
      props.clientId = databricksClientId
      props.clientSecret = databricksClientSecret
      props.azureTenantId = databricksTenantId
    }
    if (dsType === 'trino') {
      // schemas field is included in values already
    }

    if (skipMapping) {
      if (preserveDisplayName && displayNameRaw && !props.displayName) {
        props.displayName = displayNameRaw
      }
      onSubmit(props)
      return
    }

    const result = config.propertiesMapping(props)
    if (preserveDisplayName && displayNameRaw && !result.displayName) {
      result.displayName = displayNameRaw
    }
    onSubmit(result)
  }

  return (
    <div className="space-y-4">
      {config.fields.map((field) => {
        if (field.type === 'file' && dsType === 'bigquery') {
          return (
            <div key={field.name}>
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
                {field.label} {field.required && <span className="text-error">*</span>}
              </label>
              <FileUpload
                value={credentials}
                onChange={setCredentials}
                accept=".json"
                helpText={field.helpText}
              />
            </div>
          )
        }
        return (
          <div key={field.name}>
            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
              {field.label} {field.required && <span className="text-error">*</span>}
            </label>
            {renderField(field, values, setField)}
          </div>
        )
      })}

      {/* DuckDB-specific: configurations + extensions */}
      {dsType === 'duckdb' && (
        <>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
              Configuration Options
            </label>
            <KeyValueList
              value={configurations}
              onChange={setConfigurations}
              keyPlaceholder={i18nT('setup.key', 'Key')}
              valuePlaceholder={i18nT('setup.value', 'Value')}
            />
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
              Extensions
            </label>
            <StringList
              value={extensions}
              onChange={setExtensions}
              placeholder={i18nT('setup.extensionName', 'Extension name')}
            />
          </div>
        </>
      )}

      {/* Snowflake: auth method */}
      {dsType === 'snowflake' && (
        <>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
              {i18nT('setup.authenticationMethod', 'Authentication Method')}
            </label>
            <RadioGroup
              label=""
              value={snowflakeAuth}
              onChange={(v) => setSnowflakeAuth(v as 'password' | 'keypair')}
              options={[
                { label: i18nT('setup.authPassword', 'Password authentication'), value: 'password' },
                { label: i18nT('setup.authKeyPair', 'Key pair authentication'), value: 'keypair' },
              ]}
            />
          </div>
          {snowflakeAuth === 'keypair' && (
            <div>
              <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
                {i18nT('setup.privateKeyFile', 'Private Key File')} <span className="text-error">*</span>
              </label>
              <FileUpload
                value={privateKey}
                onChange={setPrivateKey}
                accept=".pem,.key,.p8"
                helpText={i18nT('setup.privateKeyHelp', 'Upload your private key file for key pair authentication')}
              />
            </div>
          )}
        </>
      )}

      {/* Athena: auth method */}
      {dsType === 'athena' && (
        <>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
              {i18nT('setup.authenticationMethod', 'Authentication Method')}
            </label>
            <RadioGroup
              label=""
              value={athenaAuth}
              onChange={setAthenaAuth}
              options={[
                { label: i18nT('setup.authAwsCredentials', 'AWS credentials'), value: 'classic' },
                { label: i18nT('setup.authOidcWebIdentity', 'OIDC (web identity token)'), value: 'oidc' },
                { label: i18nT('setup.authInstanceProfile', 'Instance Profile'), value: 'instance_profile' },
              ]}
            />
          </div>
          {athenaAuth === 'classic' && (
            <>
              <Input
                label={i18nT('setup.awsAccessKeyId', 'AWS Access Key ID')}
                value={awsAccessKey}
                onChange={(e) => setAwsAccessKey(e.target.value)}
              />
              <Input
                type="password"
                label={i18nT('setup.awsSecretAccessKey', 'AWS Secret Access Key')}
                value={awsSecretKey}
                onChange={(e) => setAwsSecretKey(e.target.value)}
              />
            </>
          )}
          {athenaAuth === 'oidc' && (
            <>
              <Input
                type="password"
                label={i18nT('setup.webIdentityToken', 'Web Identity Token')}
                value={webIdentityToken}
                onChange={(e) => setWebIdentityToken(e.target.value)}
              />
              <Input
                label={i18nT('setup.awsRoleArn', 'AWS Role ARN')}
                placeholder="arn:aws:iam::<account-id>:role/<role-name>"
                value={roleArn}
                onChange={(e) => setRoleArn(e.target.value)}
              />
              <Input
                label={i18nT('setup.roleSessionName', 'Role Session Name')}
                value={roleSessionName}
                onChange={(e) => setRoleSessionName(e.target.value)}
              />
            </>
          )}
          {athenaAuth === 'instance_profile' && (
            <p className="text-sm italic text-gray-500 dark:text-gray-400">
              {i18nT('setup.instanceProfileInfo', 'We will automatically detect AWS credentials from the Instance Profile role assigned to this compute environment (EC2, ECS, EKS).')}
            </p>
          )}
        </>
      )}

      {/* Redshift: auth method */}
      {dsType === 'redshift' && (
        <>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
              {i18nT('setup.authenticationMethod', 'Authentication Method')}
            </label>
            <RadioGroup
              label=""
              value={redshiftType}
              onChange={setRedshiftType}
              options={[
                { label: i18nT('setup.authUserPassword', 'Username and password'), value: 'redshift' },
                { label: i18nT('setup.authAwsCredentials', 'AWS credentials'), value: 'redshift_iam' },
              ]}
            />
          </div>
          {redshiftType === 'redshift_iam' && (
            <>
              <Input
                label={i18nT('setup.clusterIdentifier', 'Cluster Identifier')}
                placeholder="redshift-cluster-1"
                value={clusterIdentifier}
                onChange={(e) => setClusterIdentifier(e.target.value)}
              />
              <Input
                label={i18nT('setup.awsRegion', 'AWS Region')}
                placeholder="us-east-1"
                value={redshiftAwsRegion}
                onChange={(e) => setRedshiftAwsRegion(e.target.value)}
              />
              <Input
                label={i18nT('setup.awsAccessKeyId', 'AWS Access Key ID')}
                value={redshiftAwsAccessKey}
                onChange={(e) => setRedshiftAwsAccessKey(e.target.value)}
              />
              <Input
                type="password"
                label={i18nT('setup.awsSecretAccessKey', 'AWS Secret Access Key')}
                value={redshiftAwsSecretKey}
                onChange={(e) => setRedshiftAwsSecretKey(e.target.value)}
              />
            </>
          )}
        </>
      )}

      {/* Databricks: auth method */}
      {dsType === 'databricks' && (
        <>
          <div>
            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
              {i18nT('setup.authenticationMethod', 'Authentication Method')}
            </label>
            <RadioGroup
              label=""
              value={databricksType}
              onChange={setDatabricksType}
              options={[
                { label: i18nT('setup.authPat', 'Personal Access Token (PAT)'), value: 'token' },
                { label: i18nT('setup.authServicePrincipal', 'Service Principal'), value: 'service_principal' },
              ]}
            />
          </div>
          {databricksType === 'service_principal' && (
            <>
              <Input
                label={i18nT('setup.clientId', 'Client ID')}
                value={databricksClientId}
                onChange={(e) => setDatabricksClientId(e.target.value)}
              />
              <Input
                type="password"
                label={i18nT('setup.clientSecret', 'Client Secret')}
                value={databricksClientSecret}
                onChange={(e) => setDatabricksClientSecret(e.target.value)}
              />
              <Input
                label={i18nT('setup.azureTenantId', 'Azure Tenant ID')}
                placeholder="e.g. 72f988bf-86f1-41af-91ab-2d7cd011db47"
                value={databricksTenantId}
                onChange={(e) => setDatabricksTenantId(e.target.value)}
              />
            </>
          )}
        </>
      )}

      <div className="pt-4">
        <Button
          variant="primary"
          onClick={handleSubmit}
          loading={loading}
          className="w-full"
        >
          {submitLabel ?? i18nT('setup.createAndNext', 'Create & Next')}
        </Button>
      </div>
    </div>
  )
}
