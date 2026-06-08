'use client'

import { useParams, useRouter } from 'next/navigation'
import { useEffect, useState, useCallback, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { projectsApi, modelingApi } from '@/lib/api'
import { useI18nStore } from '@/stores/i18nStore'
import { DATASOURCE_CONFIGS, getDatasourceConfig } from '@/lib/datasourceConfig'
import { SAMPLE_DATASETS, SAMPLE_DATASET_LIST, getInitSql, getSampleTableDetails } from '@/lib/sampleDatasets'
import { Tabs } from '@/components/ui/Tabs'
import { Button } from '@/components/ui/Button'
import { Input } from '@/components/ui/Input'
import { Modal } from '@/components/ui/Modal'
import { Skeleton } from '@/components/ui/Skeleton'
import { EmptyState } from '@/components/ui/EmptyState'
import { ErrorToast } from '@/components/ui/ErrorToast'
import { useToast } from '@/components/ui/Toast'
import { Table, type Column } from '@/components/ui/Table'
import ConnectionForm from '@/components/setup/ConnectionForm'

const DEFAULT_PROJECT_PROMPT = `You are working in PrismBI project "{{display_name}}".

### PROJECT DESCRIPTION ###
{{description}}

### SEMANTIC MODEL ###
{{semantic_model}}

### VERIFIED SQL EXAMPLES ###
{{sql_examples}}`

export default function ProjectSettingsClient() {
  const t = useI18nStore((s) => s.t)
  const params = useParams<{ id: string }>()
  const router = useRouter()
  const projectId = Number(params.id) || 0
  const [activeTab, setActiveTab] = useState('datasources')
  const [error, setError] = useState<string | null>(null)
  const { toast } = useToast()
  const queryClient = useQueryClient()

  const [showEditModal, setShowEditModal] = useState(false)
  const [editName, setEditName] = useState('')
  const [editDisplayName, setEditDisplayName] = useState('')
  const [generalName, setGeneralName] = useState('')
  const [generalDisplayName, setGeneralDisplayName] = useState('')
  const [generalDescription, setGeneralDescription] = useState('')
  const [generalPrompt, setGeneralPrompt] = useState(DEFAULT_PROJECT_PROMPT)

  const [showAddMemberModal, setShowAddMemberModal] = useState(false)
  const [memberUserId, setMemberUserId] = useState('')
  const [memberRoleId, setMemberRoleId] = useState('')

  const [showChangeDatasourceModal, setShowChangeDatasourceModal] = useState(false)
  const [changeDatasourceModelId, setChangeDatasourceModelId] = useState<number | null>(null)
  const [changeDatasourceModelName, setChangeDatasourceModelName] = useState('')
  const [changeDatasourceBindingId, setChangeDatasourceBindingId] = useState<number | null>(null)

  const [showAddDatasourceModal, setShowAddDatasourceModal] = useState(false)
  const [addDsType, setAddDsType] = useState('')
  const [addDsFormValues, setAddDsFormValues] = useState<Record<string, unknown>>({})
  const [addDsSubmitting, setAddDsSubmitting] = useState(false)
  const [addDsCategory, setAddDsCategory] = useState<'manual' | 'sample'>('manual')
  const [addDsSampleKeys, setAddDsSampleKeys] = useState<Set<string>>(new Set())

  const {
    data: project,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => projectsApi.get(projectId),
  })

  const { data: projectDatasources = [] } = useQuery({
    queryKey: ['project-datasources', projectId],
    queryFn: () => projectsApi.datasources.list(projectId) as Promise<any[]>,
    enabled: projectId > 0,
  })

  const { data: projectMembers = [] } = useQuery({
    queryKey: ['project-members', projectId],
    queryFn: () => projectsApi.members.list(projectId) as Promise<any[]>,
    enabled: projectId > 0,
  })

  const { data: modelBindingStatus } = useQuery({
    queryKey: ['project-model-binding-status', projectId],
    queryFn: () => modelingApi.bindingStatus(projectId) as Promise<any>,
    enabled: projectId > 0,
  })

  const proj = project as any

  const existingSampleKeys = useMemo(() => {
    const keys = new Set<string>()
    for (const ds of projectDatasources as any[]) {
      const name: string = ds.datasource_name || ds.name || ds.alias || ds.properties?.name || ''
      if (name.startsWith('sample_')) {
        const suffix = name.slice('sample_'.length)
        const key = suffix.replace(/_\d+$/, '')
        if (SAMPLE_DATASETS[key]) keys.add(key)
      }
    }
    return keys
  }, [projectDatasources])

  useEffect(() => {
    if (!proj) return
    setGeneralName(proj.name ?? '')
    setGeneralDisplayName(proj.display_name ?? proj.displayName ?? '')
    setGeneralDescription(proj.description ?? '')
    setGeneralPrompt(proj.prompt ?? DEFAULT_PROJECT_PROMPT)
  }, [proj?.name, proj?.display_name, proj?.description, proj?.prompt])

  const updateProjectMutation = useMutation({
    mutationFn: (data: { name?: string; display_name?: string; description?: string; prompt?: string }) =>
      projectsApi.update(projectId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['project', projectId] })
      toast(t('project.updated', 'Project updated'), 'success')
      setShowEditModal(false)
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('project.failedToUpdate', 'Failed to update project'), 'error'),
  })

  const unbindDatasourceMutation = useMutation({
    mutationFn: (bindingId: number) =>
      projectsApi.datasources.unbind(projectId, bindingId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['project', projectId] })
      queryClient.invalidateQueries({ queryKey: ['project-datasources', projectId] })
      toast(t('project.datasourceUnbound', 'Datasource unbound'), 'success')
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('project.failedToUnbind', 'Failed to unbind datasource'), 'error'),
  })

  const handleAddDatasource = useCallback(async () => {
    if (addDsCategory === 'sample') {
      const selectedKeys = Array.from(addDsSampleKeys).filter((k) => !existingSampleKeys.has(k))
      if (selectedKeys.length === 0) {
        toast(t('setup.selectSampleDataset', 'Select at least one sample dataset'), 'warning')
        return
      }
      setAddDsSubmitting(true)
      let addedCount = 0
      for (const key of selectedKeys) {
        const ds = SAMPLE_DATASETS[key]
        if (!ds) continue
        try {
          await projectsApi.datasources.register(projectId, {
            name: `sample_${key}`,
            type: 'duckdb',
            properties: {
              dbname: `sample_${key}_${projectId}`,
              initSql: getInitSql(key),
              displayName: ds.displayName,
              sampleTableDetails: getSampleTableDetails(key),
            },
          })
          addedCount++
        } catch (err) {
          toast(err instanceof Error ? err.message : t('project.failedToAddDatasource', 'Failed to add datasource'), 'error')
        }
      }
      if (addedCount > 0) {
        queryClient.invalidateQueries({ queryKey: ['project-datasources', projectId] })
        queryClient.invalidateQueries({ queryKey: ['project', projectId] })
        toast(t('project.datasourceAdded', 'Datasource added successfully'), 'success')
        setShowAddDatasourceModal(false)
        setAddDsSampleKeys(new Set())
      }
      setAddDsSubmitting(false)
      return
    }

    if (!addDsType) {
      toast(t('project.selectDatasourceType', 'Please select a datasource type'), 'warning')
      return
    }
    const config = getDatasourceConfig(addDsType)
    if (!config) return
    const mappedProps = config.propertiesMapping(addDsFormValues)
    const name = String(addDsFormValues.displayName || config.displayName)
    setAddDsSubmitting(true)
    try {
      await projectsApi.datasources.register(projectId, {
        name,
        type: addDsType,
        properties: { ...mappedProps, displayName: name },
      })
      queryClient.invalidateQueries({ queryKey: ['project-datasources', projectId] })
      queryClient.invalidateQueries({ queryKey: ['project', projectId] })
      toast(t('project.datasourceAdded', 'Datasource added successfully'), 'success')
      setShowAddDatasourceModal(false)
      setAddDsType('')
      setAddDsFormValues({})
    } catch (err) {
      toast(err instanceof Error ? err.message : t('project.failedToAddDatasource', 'Failed to add datasource'), 'error')
    } finally {
      setAddDsSubmitting(false)
    }
  }, [addDsCategory, addDsSampleKeys, addDsType, addDsFormValues, projectId, queryClient, toast, t])

  const addMemberMutation = useMutation({
    mutationFn: (data: { user_id: number; role_id: number }) =>
      projectsApi.members.add(projectId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['project', projectId] })
      queryClient.invalidateQueries({ queryKey: ['project-members', projectId] })
      toast(t('project.memberAdded', 'Member added'), 'success')
      setShowAddMemberModal(false)
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('project.failedToAddMember', 'Failed to add member'), 'error'),
  })

  const changeDatasourceMutation = useMutation({
    mutationFn: (data: { modelId: number; sourceBindingId: number }) =>
      modelingApi.models.update(projectId, data.modelId, { source_binding_id: data.sourceBindingId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['project-model-binding-status', projectId] })
      toast(t('project.datasourceChanged', 'Datasource changed successfully'), 'success')
      setShowChangeDatasourceModal(false)
      setChangeDatasourceModelId(null)
      setChangeDatasourceBindingId(null)
    },
    onError: (err) =>
      toast(err instanceof Error ? err.message : t('project.failedToChangeDatasource', 'Failed to change datasource'), 'error'),
  })

  const TABS = [
    { key: 'datasources', label: t('datasource.title', 'Datasources') },
    { key: 'models', label: t('project.models', 'Models') },
    { key: 'members', label: t('project.members', 'Members') },
    { key: 'general', label: t('project.general', 'General') },
  ]

  const memberColumns: Column<any>[] = [
    {
      key: 'user',
      header: t('project.memberUser', 'User'),
      render: (member) => (
        <div>
          <p className="font-medium text-gray-900 dark:text-gray-100">{member.display_name || member.username}</p>
          <p className="text-xs text-gray-500 dark:text-gray-400">{member.username}</p>
        </div>
      ),
    },
    { key: 'role_name', header: t('project.memberRole', 'Role') },
    { key: 'created_at', header: t('project.memberJoinedAt', 'Joined At') },
  ]

  if (isLoading) {
    return (
      <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
        <Skeleton className="mb-6 h-10 w-full" />
        <Skeleton className="h-48 w-full" />
      </div>
    )
  }

  if (isError || !project) {
    return (
      <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
        <ErrorToast
          message={t('project.failedToLoadSettings', 'Failed to load project settings')}
          onRetry={() => refetch()}
          onClose={() => setError(null)}
        />
      </div>
    )
  }

  return (
    <div className="min-h-full rounded-xl border border-gray-200 bg-white p-2 dark:border-gray-700 dark:bg-gray-900">
      {error && <ErrorToast message={error} onClose={() => setError(null)} />}

      <Tabs tabs={TABS} activeKey={activeTab} onChange={setActiveTab} />

      <div className="mt-6">
        {activeTab === 'datasources' && (
          <div>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
              {projectDatasources.map((ds: any) => {
                const dsIcon = getDatasourceConfig(ds.type || ds.datasource_type)?.icon
                return (
                  <div key={ds.bindingId ?? ds.binding_id ?? ds.id} className="group relative flex flex-col items-center rounded-lg border border-gray-200 bg-white p-4 transition-shadow hover:shadow-md dark:border-gray-700 dark:bg-gray-800">
                    {dsIcon && <img src={dsIcon} alt="" className="mb-2 h-8 w-8 object-contain" />}
                    <p className="max-w-full truncate text-center text-sm font-medium text-gray-900 dark:text-gray-100">{ds.alias || ds.name || ds.datasource_name}</p>
                    <p className="text-xs text-gray-500 dark:text-gray-400">{ds.type || ds.datasource_type}</p>
                    {ds.datasource_name && ds.alias && ds.alias !== ds.datasource_name && (
                      <p className="max-w-full truncate text-xs text-gray-400">{ds.datasource_name}</p>
                    )}
                  </div>
                )
              })}
              <button
                onClick={() => { setAddDsType(''); setAddDsFormValues({}); setAddDsCategory('manual'); setAddDsSampleKeys(new Set()); setShowAddDatasourceModal(true) }}
                className="flex min-h-[100px] flex-col items-center justify-center rounded-lg border-2 border-dashed border-gray-300 bg-gray-50 text-gray-500 transition-colors hover:border-primary-400 hover:bg-primary-50 hover:text-primary-600 dark:border-gray-600 dark:bg-gray-900 dark:hover:border-primary-500 dark:hover:bg-gray-800 dark:hover:text-primary-400"
              >
                <svg className="mb-1 h-8 w-8" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
                </svg>
                <span className="text-sm font-medium">{t('project.addDatasource', 'Add Datasource')}</span>
              </button>
            </div>
          </div>
        )}
        {activeTab === 'models' && (
          <div>
            {modelBindingStatus ? (
              <div className="space-y-4">
                <div className="rounded-lg border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-800">
                  <div className="grid grid-cols-4 gap-4 text-center">
                    <div>
                      <p className="text-2xl font-bold text-gray-900 dark:text-gray-100">{modelBindingStatus.total_models}</p>
                      <p className="text-sm text-gray-500 dark:text-gray-400">{t('project.totalModels', 'Total Models')}</p>
                    </div>
                    <div>
                      <p className="text-2xl font-bold text-green-600 dark:text-green-400">{modelBindingStatus.bound_models}</p>
                      <p className="text-sm text-gray-500 dark:text-gray-400">{t('project.boundModels', 'Bound')}</p>
                    </div>
                    <div>
                      <p className="text-2xl font-bold text-red-600 dark:text-red-400">{modelBindingStatus.unbound_models}</p>
                      <p className="text-sm text-gray-500 dark:text-gray-400">{t('project.unboundModels', 'Unbound')}</p>
                    </div>
                    <div>
                      <p className="text-2xl font-bold text-blue-600 dark:text-blue-400">{modelBindingStatus.valid_bindings}</p>
                      <p className="text-sm text-gray-500 dark:text-gray-400">{t('project.validBindings', 'Valid Bindings')}</p>
                    </div>
                  </div>
                </div>

                {modelBindingStatus.models.unbound.length > 0 && (
                  <div className="rounded-lg border border-red-200 bg-red-50 p-4 dark:border-red-800 dark:bg-red-900/20">
                    <h3 className="mb-2 font-semibold text-red-800 dark:text-red-300">
                      {t('project.unboundModelsWarning', 'Models Without Datasource Binding')}
                    </h3>
                    <p className="mb-3 text-sm text-red-700 dark:text-red-400">
                      {t('project.unboundModelsDescription', 'These models cannot be queried because they are not mapped to a datasource. Please edit them in the Modeling page to assign a datasource binding.')}
                    </p>
                    <div className="space-y-2">
                      {modelBindingStatus.models.unbound.map((model: any) => (
                        <div key={model.id} className="flex items-center justify-between rounded border border-red-200 bg-white p-3 dark:border-red-700 dark:bg-gray-800">
                          <div>
                            <p className="font-medium text-gray-900 dark:text-gray-100">{model.display_name || model.name}</p>
                            <p className="text-xs text-gray-500 dark:text-gray-400">
                              {model.name} • {model.table_reference || 'N/A'} • {model.issue}
                            </p>
                          </div>
                          <button
                            onClick={() => {
                              setChangeDatasourceModelId(model.id)
                              setChangeDatasourceModelName(model.display_name || model.name)
                              setChangeDatasourceBindingId(null)
                              setShowChangeDatasourceModal(true)
                            }}
                            className="rounded bg-blue-500 px-3 py-1 text-xs font-medium text-white hover:bg-blue-600"
                          >
                            {t('project.changeDatasource', 'Change Datasource')}
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {modelBindingStatus.models.bound.length > 0 && (
                  <div>
                    <h3 className="mb-2 font-semibold text-gray-900 dark:text-gray-100">
                      {t('project.boundModelsList', 'Bound Models')}
                    </h3>
                    <div className="space-y-2">
                      {modelBindingStatus.models.bound.map((model: any) => (
                        <div key={model.id} className="flex items-center justify-between rounded border border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-800">
                          <div>
                            <p className="font-medium text-gray-900 dark:text-gray-100">{model.display_name || model.name}</p>
                            <p className="text-xs text-gray-500 dark:text-gray-400">
                              {model.name} • {model.table_reference || 'N/A'} • Binding ID: {model.source_binding_id}
                            </p>
                          </div>
                          <div className="flex items-center gap-2">
                            <span className="rounded-full bg-green-100 px-2 py-1 text-xs font-medium text-green-800 dark:bg-green-900/30 dark:text-green-400">
                              {t('project.valid', 'Valid')}
                            </span>
                            <button
                              onClick={() => {
                                setChangeDatasourceModelId(model.id)
                                setChangeDatasourceModelName(model.display_name || model.name)
                                setChangeDatasourceBindingId(model.source_binding_id)
                                setShowChangeDatasourceModal(true)
                              }}
                              className="rounded bg-gray-200 px-3 py-1 text-xs font-medium text-gray-700 hover:bg-gray-300 dark:bg-gray-700 dark:text-gray-300 dark:hover:bg-gray-600"
                            >
                              {t('project.changeDatasource', 'Change Datasource')}
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {modelBindingStatus.total_models === 0 && (
                  <EmptyState
                    message={t('project.noModels', 'No models in this project.')}
                    action={{ label: t('project.goToModeling', 'Go to Modeling'), onClick: () => router.push('/modeling') }}
                  />
                )}
              </div>
            ) : (
              <Skeleton className="h-48 w-full" />
            )}
          </div>
        )}
        {activeTab === 'members' && (
          <div>
            <div className="mb-4 flex justify-end">
              <Button onClick={() => { setShowAddMemberModal(true); setMemberUserId(''); setMemberRoleId('') }}>
                {t('project.addMember', 'Add member')}
              </Button>
            </div>
            {projectMembers.length > 0 ? (
              <Table columns={memberColumns} data={projectMembers as any[]} />
            ) : (
              <EmptyState
                message={t('project.noMembers', 'No members yet.')}
                action={{ label: t('project.addMember', 'Add member'), onClick: () => { setShowAddMemberModal(true); setMemberUserId(''); setMemberRoleId('') } }}
              />
            )}
          </div>
        )}
        {activeTab === 'general' && (
          <div className="space-y-4 rounded-lg border border-gray-200 bg-white p-4 dark:border-gray-700 dark:bg-gray-900">
            <Input
              label={t('project.name', 'Project Name')}
              value={generalName}
              onChange={(e) => setGeneralName(e.target.value)}
            />
            <Input
              label={t('project.displayName', 'Display Name')}
              value={generalDisplayName}
              onChange={(e) => setGeneralDisplayName(e.target.value)}
              placeholder={t('project.displayNamePlaceholder', 'Enter a display name for this project')}
            />
            <div>
              <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">
                {t('project.description', 'Project Description')}
              </label>
              <textarea
                value={generalDescription}
                onChange={(e) => setGeneralDescription(e.target.value)}
                rows={4}
                className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
                placeholder={t('project.descriptionPlaceholder', 'Describe the business domain, key metrics, and intended users of this project.')}
              />
            </div>
            <div>
              <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">
                {t('project.prompt', 'Project Prompt')}
              </label>
              <textarea
                value={generalPrompt}
                onChange={(e) => setGeneralPrompt(e.target.value)}
                rows={8}
                className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
              />
              <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                {t('project.promptVars', 'Variables: {{name}}, {{display_name}}, {{description}}, {{semantic_model}}, {{datasource_count}}, {{model_count}}, {{current_date}}, {{current_datetime}}')}
              </p>
            </div>
            <div className="flex justify-between gap-2">
              <Button
                variant="secondary"
                onClick={() => {
                  setGeneralPrompt(DEFAULT_PROJECT_PROMPT)
                }}
              >
                {t('project.resetPrompt', 'Reset Prompt')}
              </Button>
              <Button
                onClick={() => updateProjectMutation.mutate({
                  name: generalName,
                  display_name: generalDisplayName,
                  description: generalDescription,
                  prompt: generalPrompt,
                })}
                loading={updateProjectMutation.isPending}
              >
                {t('common.save', 'Save')}
              </Button>
            </div>
          </div>
        )}
      </div>
      <Modal open={showEditModal} onClose={() => setShowEditModal(false)} title={t('project.edit', 'Edit Project')}>
        <div className="space-y-4">
          <Input label={t('project.name', 'Name')} value={editName} onChange={(e) => setEditName(e.target.value)} />
          <Input label={t('project.displayName', 'Display Name')} value={editDisplayName} onChange={(e) => setEditDisplayName(e.target.value)} />
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setShowEditModal(false)}>{t('common.cancel', 'Cancel')}</Button>
            <Button
              onClick={() => updateProjectMutation.mutate({ name: editName, display_name: editDisplayName })}
              loading={updateProjectMutation.isPending}
            >
              {t('common.save', 'Save')}
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={showAddMemberModal} onClose={() => setShowAddMemberModal(false)} title={t('project.addMember', 'Add Member')}>
        <div className="space-y-4">
          <Input
            label={t('project.userId', 'User ID')}
            type="number"
            value={memberUserId}
            onChange={(e) => setMemberUserId(e.target.value)}
            placeholder={t('project.userIdPlaceholder', 'Enter user ID')}
          />
          <Input
            label={t('project.roleId', 'Role ID')}
            type="number"
            value={memberRoleId}
            onChange={(e) => setMemberRoleId(e.target.value)}
            placeholder={t('project.roleIdPlaceholder', 'Enter role ID')}
          />
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setShowAddMemberModal(false)}>{t('common.cancel', 'Cancel')}</Button>
            <Button
              onClick={() => {
                if (!memberUserId || !memberRoleId) {
                  toast(t('project.pleaseFillFields', 'Please fill in all fields'), 'warning')
                  return
                }
                addMemberMutation.mutate({ user_id: Number(memberUserId), role_id: Number(memberRoleId) })
              }}
              loading={addMemberMutation.isPending}
            >
              {t('common.add', 'Add')}
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={showChangeDatasourceModal} onClose={() => setShowChangeDatasourceModal(false)} title={t('project.changeDatasource', 'Change Datasource')}>
        <div className="space-y-4">
          <div>
            <p className="mb-1 text-sm font-medium text-gray-700 dark:text-gray-300">
              {t('project.model', 'Model')}: <span className="font-semibold">{changeDatasourceModelName}</span>
            </p>
          </div>
          <div>
            <label className="mb-2 block text-sm font-medium text-gray-700 dark:text-gray-300">
              {t('project.selectDatasource', 'Select Datasource')}
            </label>
            <select
              value={changeDatasourceBindingId ?? ''}
              onChange={(e) => setChangeDatasourceBindingId(e.target.value ? Number(e.target.value) : null)}
              className="block w-full rounded-md border border-gray-300 bg-white px-3 py-2 text-sm text-gray-900 focus:outline-none focus:ring-2 focus:ring-primary-300 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
            >
              <option value="">{t('project.selectDatasource', 'Select Datasource')}</option>
              {projectDatasources.map((ds: any) => {
                const bindingId = ds.bindingId ?? ds.binding_id ?? ds.id
                const name = ds.alias || ds.name || ds.datasource_name
                const type = ds.type || ds.datasource_type
                return (
                  <option key={bindingId} value={bindingId}>
                    {name} ({type})
                  </option>
                )
              })}
            </select>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => setShowChangeDatasourceModal(false)}>{t('common.cancel', 'Cancel')}</Button>
            <Button
              onClick={() => {
                if (changeDatasourceModelId === null || changeDatasourceBindingId === null) {
                  toast(t('project.pleaseFillFields', 'Please fill in all fields'), 'warning')
                  return
                }
                changeDatasourceMutation.mutate({ modelId: changeDatasourceModelId, sourceBindingId: changeDatasourceBindingId })
              }}
              loading={changeDatasourceMutation.isPending}
            >
              {t('common.save', 'Save')}
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={showAddDatasourceModal} onClose={() => setShowAddDatasourceModal(false)} title={t('project.addDatasource', 'Add Datasource')}>
        <div className="space-y-4">
          {proj?.type === 'sample' && (
            <div className="flex gap-2 border-b border-gray-200 pb-3 dark:border-gray-700">
              <button
                type="button"
                onClick={() => { setAddDsCategory('sample'); setAddDsType(''); setAddDsFormValues({}) }}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${addDsCategory === 'sample' ? 'bg-primary-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700'}`}
              >
                {t('project.sampleDatasources', 'Sample Datasets')}
              </button>
              <button
                type="button"
                onClick={() => { setAddDsCategory('manual'); setAddDsSampleKeys(new Set()) }}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${addDsCategory === 'manual' ? 'bg-primary-600 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:hover:bg-gray-700'}`}
              >
                {t('project.manualDatasources', 'Custom Connection')}
              </button>
            </div>
          )}

          {addDsCategory === 'sample' ? (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                {SAMPLE_DATASET_LIST.map((ds) => {
                    const alreadyAdded = existingSampleKeys.has(ds.key)
                    const selected = alreadyAdded || addDsSampleKeys.has(ds.key)
                    return (
                      <button
                        key={ds.key}
                        type="button"
                        disabled={alreadyAdded}
                        onClick={() => {
                          if (alreadyAdded) return
                          setAddDsSampleKeys((prev) => {
                            const next = new Set(prev)
                            if (next.has(ds.key)) next.delete(ds.key)
                            else next.add(ds.key)
                            return next
                          })
                        }}
                        className={`rounded-lg border p-3 text-left transition-colors ${alreadyAdded ? 'cursor-not-allowed border-green-400 bg-green-50 dark:border-green-500 dark:bg-green-900/20' : selected ? 'border-green-500 bg-green-100 dark:border-green-400 dark:bg-green-900/30' : 'border-gray-200 bg-white hover:border-green-300 dark:border-gray-700 dark:bg-gray-800 dark:hover:border-green-500'}`}
                      >
                        <div className="flex items-center gap-1">
                          <p className="text-sm font-semibold text-gray-900 dark:text-gray-100">{ds.displayName}</p>
                          {alreadyAdded && <span className="text-xs text-green-600 dark:text-green-400">{t('project.datasourceAlreadyAdded', 'Added')}</span>}
                        </div>
                        <p className="text-xs text-gray-500 dark:text-gray-400">{ds.tableCount} tables</p>
                      </button>
                    )
                  })}
              </div>
              <div className="flex justify-end gap-2">
                <Button variant="secondary" onClick={() => setShowAddDatasourceModal(false)}>{t('common.cancel', 'Cancel')}</Button>
                <Button
                  onClick={() => handleAddDatasource()}
                  loading={addDsSubmitting}
                  disabled={Array.from(addDsSampleKeys).filter((k) => !existingSampleKeys.has(k)).length === 0}
                >
                  {t('project.addDatasource', 'Add Datasource')}
                </Button>
              </div>
            </div>
          ) : !addDsType ? (
            <div className="grid grid-cols-3 gap-3 sm:grid-cols-4 md:grid-cols-5">
              {Object.values(DATASOURCE_CONFIGS).map((cfg) => (
                <button
                  key={cfg.key}
                  type="button"
                  onClick={() => setAddDsType(cfg.key)}
                  className="flex flex-col items-center gap-1 rounded-lg border border-gray-200 p-3 transition-colors hover:border-primary-400 hover:bg-primary-50 dark:border-gray-700 dark:hover:border-primary-500 dark:hover:bg-gray-800"
                >
                  <img src={cfg.icon} alt={cfg.displayName} className="h-8 w-8 object-contain" />
                  <span className="text-xs font-medium text-gray-700 dark:text-gray-300">{cfg.displayName}</span>
                </button>
              ))}
            </div>
          ) : (
            <div>
              <div className="mb-3 flex items-center gap-2">
                <img src={getDatasourceConfig(addDsType)?.icon} alt="" className="h-6 w-6 object-contain" />
                <span className="font-medium text-gray-900 dark:text-gray-100">{getDatasourceConfig(addDsType)?.displayName}</span>
                <Button variant="secondary" size="sm" onClick={() => setAddDsType('')}>{t('common.back', 'Back')}</Button>
              </div>
              <ConnectionForm
                dsType={addDsType}
                onSubmit={(values) => { setAddDsFormValues(values); handleAddDatasource() }}
                loading={addDsSubmitting}
              />
            </div>
          )}
        </div>
      </Modal>
    </div>
  )
}