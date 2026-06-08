'use client'

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { adminSecurityPoliciesApi, adminRolesApi, projectsApi, modelingApi, type RowSecurityPolicy, type ColumnSecurityPolicy } from '@/lib/api'
import { useAuthStore } from '@/stores/authStore'
import { useI18nStore } from '@/stores/i18nStore'
import { useProjectStore } from '@/stores/projectStore'
import { Button } from '@/components/ui/Button'
import { Skeleton } from '@/components/ui/Skeleton'
import { RequirePermission } from '@/components/providers/RequirePermission'

export default function SecurityPoliciesPage() {
  return (
    <RequirePermission resource="security_policies" action="read">
      <SecurityPoliciesContent />
    </RequirePermission>
  )
}

function SecurityPoliciesContent() {
  const t = useI18nStore((s) => s.t)
  const [tab, setTab] = useState<'rls' | 'cls'>('rls')
  const [projectId, setProjectId] = useState<number | undefined>(undefined)
  const [roleId, setRoleId] = useState<number | undefined>(undefined)
  const [showCreate, setShowCreate] = useState(false)
  const [editPolicy, setEditPolicy] = useState<RowSecurityPolicy | ColumnSecurityPolicy | null>(null)
  const [error, setError] = useState<string | null>(null)
  const queryClient = useQueryClient()
  const currentProject = useProjectStore((s) => s.currentProject)

  const { data: rlsPolicies = [], isLoading: rlsLoading } = useQuery({
    queryKey: ['rls-policies', projectId, roleId],
    queryFn: () => adminSecurityPoliciesApi.rls.list({ project_id: projectId, role_id: roleId }),
  })

  const { data: clsPolicies = [], isLoading: clsLoading } = useQuery({
    queryKey: ['cls-policies', projectId, roleId],
    queryFn: () => adminSecurityPoliciesApi.cls.list({ project_id: projectId, role_id: roleId }),
  })

  const { data: rolesData } = useQuery({
    queryKey: ['admin-roles'],
    queryFn: () => adminRolesApi.list(),
  })
  const roles = rolesData?.roles ?? []

  const { data: projectsData } = useQuery({
    queryKey: ['projects'],
    queryFn: () => projectsApi.list(),
  })
  const projects = Array.isArray(projectsData) ? projectsData : (projectsData?.items ?? [])

  const deleteRlsMutation = useMutation({
    mutationFn: (id: number) => adminSecurityPoliciesApi.rls.delete(id),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['rls-policies'] }); setError(null) },
    onError: () => setError(t('admin.securityPolicies.failedToDelete', 'Failed to delete policy')),
  })

  const deleteClsMutation = useMutation({
    mutationFn: (id: number) => adminSecurityPoliciesApi.cls.delete(id),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['cls-policies'] }); setError(null) },
    onError: () => setError(t('admin.securityPolicies.failedToDelete', 'Failed to delete policy')),
  })

  const isLoading = tab === 'rls' ? rlsLoading : clsLoading
  const policies = tab === 'rls' ? rlsPolicies : clsPolicies

  if (isLoading) {
    return (
      <div className="min-h-full rounded-xl border border-gray-200 bg-white p-6 dark:border-gray-700 dark:bg-gray-900">
        <div className="space-y-4">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-10 w-full" />
          {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-14" />)}
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-full rounded-xl border border-gray-200 bg-white p-6 dark:border-gray-700 dark:bg-gray-900">
      <div className="mb-6">
        <h1 className="text-xl font-bold text-gray-900 dark:text-gray-100">
          {t('admin.securityPolicies.title', 'Security Policies')}
        </h1>
        <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
          {t('admin.securityPolicies.descriptionText', 'Manage row-level and column-level security policies for your projects.')}
        </p>
      </div>

      {error && (
        <div className="mb-4 rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-900/20 dark:text-red-400">
          {error}
          <button onClick={() => setError(null)} className="ml-2 underline">{t('common.dismiss', 'Dismiss')}</button>
        </div>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="flex rounded-lg border border-gray-200 dark:border-gray-700">
          <button
            onClick={() => setTab('rls')}
            className={`px-4 py-2 text-sm font-medium transition-colors ${tab === 'rls' ? 'bg-primary-50 text-primary-700 dark:bg-primary-900/20 dark:text-primary-400' : 'text-gray-600 hover:bg-gray-50 dark:text-gray-400'}`}
          >
            {t('admin.securityPolicies.rowLevel', 'Row-Level')}
          </button>
          <button
            onClick={() => setTab('cls')}
            className={`px-4 py-2 text-sm font-medium transition-colors ${tab === 'cls' ? 'bg-primary-50 text-primary-700 dark:bg-primary-900/20 dark:text-primary-400' : 'text-gray-600 hover:bg-gray-50 dark:text-gray-400'}`}
          >
            {t('admin.securityPolicies.columnLevel', 'Column-Level')}
          </button>
        </div>

        <select
          value={projectId ?? ''}
          onChange={(e) => setProjectId(Number(e.target.value) || 0)}
          className="rounded border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800"
        >
          <option value="">{t('admin.securityPolicies.allProjects', 'All projects')}</option>
          {projects.map((p: any) => <option key={p.id} value={p.id}>{p.display_name || p.name}</option>)}
        </select>

        <select
          value={roleId ?? ''}
          onChange={(e) => setRoleId(Number(e.target.value) || 0)}
          className="rounded border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-800"
        >
          <option value="">{t('admin.securityPolicies.allRoles', 'All roles')}</option>
          {roles.map((r: any) => <option key={r.id} value={r.id}>{r.name}</option>)}
        </select>

        <div className="flex-1" />
        <Button size="sm" onClick={() => { setEditPolicy(null); setShowCreate(true) }}>
          {t('admin.securityPolicies.createPolicy', 'Create Policy')}
        </Button>
      </div>

      {showCreate && tab === 'rls' && (
        <RlsPolicyForm
          policy={null}
          projects={projects}
          roles={roles}
          currentProjectId={currentProject?.id}
          onClose={() => setShowCreate(false)}
          onSaved={() => { setShowCreate(false); queryClient.invalidateQueries({ queryKey: ['rls-policies'] }) }}
          onError={(msg: string) => setError(msg)}
        />
      )}
      {showCreate && tab === 'cls' && (
        <ClsPolicyForm
          policy={null}
          projects={projects}
          roles={roles}
          currentProjectId={currentProject?.id}
          onClose={() => setShowCreate(false)}
          onSaved={() => { setShowCreate(false); queryClient.invalidateQueries({ queryKey: ['cls-policies'] }) }}
          onError={(msg: string) => setError(msg)}
        />
      )}
      {editPolicy && tab === 'rls' && (
        <RlsPolicyForm
          policy={editPolicy as RowSecurityPolicy}
          projects={projects}
          roles={roles}
          currentProjectId={currentProject?.id}
          onClose={() => setEditPolicy(null)}
          onSaved={() => { setEditPolicy(null); queryClient.invalidateQueries({ queryKey: ['rls-policies'] }) }}
          onError={(msg: string) => setError(msg)}
        />
      )}
      {editPolicy && tab === 'cls' && (
        <ClsPolicyForm
          policy={editPolicy as ColumnSecurityPolicy}
          projects={projects}
          roles={roles}
          currentProjectId={currentProject?.id}
          onClose={() => setEditPolicy(null)}
          onSaved={() => { setEditPolicy(null); queryClient.invalidateQueries({ queryKey: ['cls-policies'] }) }}
          onError={(msg: string) => setError(msg)}
        />
      )}

      {policies.length === 0 ? (
        <div className="py-12 text-center text-sm text-gray-500 dark:text-gray-400">
          {t('admin.securityPolicies.noPolicies', 'No policies found. Create one above.')}
        </div>
      ) : tab === 'rls' ? (
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
            <thead className="bg-gray-50 dark:bg-gray-800">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">{t('admin.securityPolicies.model', 'Model')}</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">{t('admin.securityPolicies.column', 'Column')}</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">{t('admin.securityPolicies.operator', 'Operator')}</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">{t('admin.securityPolicies.value', 'Value')}</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">{t('admin.securityPolicies.valueSource', 'Source')}</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">{t('admin.securityPolicies.enabled', 'Enabled')}</th>
                <th className="px-4 py-3 text-right text-xs font-medium uppercase text-gray-500">{t('common.actions', 'Actions')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {rlsPolicies.map((p: RowSecurityPolicy) => (
                <tr key={p.id} className="hover:bg-gray-50 dark:hover:bg-gray-800">
                  <td className="whitespace-nowrap px-4 py-3 text-sm">{p.model_name}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm">{p.column_name}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm">{p.operator}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm">{p.value ?? '—'}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm">{p.value_source}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm">
                    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${p.is_enabled ? 'bg-green-100 text-green-800 dark:bg-green-900/20 dark:text-green-400' : 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400'}`}>
                      {p.is_enabled ? t('common.enabled', 'Enabled') : t('common.disabled', 'Disabled')}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-right text-sm">
                    <Button variant="secondary" size="sm" className="mr-2" onClick={() => setEditPolicy(p)}>{t('common.edit', 'Edit')}</Button>
                    <Button variant="danger" size="sm" onClick={() => deleteRlsMutation.mutate(p.id)}>{t('common.delete', 'Delete')}</Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
            <thead className="bg-gray-50 dark:bg-gray-800">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">{t('admin.securityPolicies.model', 'Model')}</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">{t('admin.securityPolicies.column', 'Column')}</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">{t('admin.securityPolicies.accessType', 'Access')}</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">{t('admin.securityPolicies.maskWith', 'Mask With')}</th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase text-gray-500">{t('admin.securityPolicies.enabled', 'Enabled')}</th>
                <th className="px-4 py-3 text-right text-xs font-medium uppercase text-gray-500">{t('common.actions', 'Actions')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {clsPolicies.map((p: ColumnSecurityPolicy) => (
                <tr key={p.id} className="hover:bg-gray-50 dark:hover:bg-gray-800">
                  <td className="whitespace-nowrap px-4 py-3 text-sm">{p.model_name}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm">{p.column_name}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm">
                    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${p.access_type === 'HIDE' ? 'bg-red-100 text-red-800 dark:bg-red-900/20 dark:text-red-400' : 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/20 dark:text-yellow-400'}`}>
                      {p.access_type}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm">{p.mask_with ?? '—'}</td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm">
                    <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${p.is_enabled ? 'bg-green-100 text-green-800 dark:bg-green-900/20 dark:text-green-400' : 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400'}`}>
                      {p.is_enabled ? t('common.enabled', 'Enabled') : t('common.disabled', 'Disabled')}
                    </span>
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-right text-sm">
                    <Button variant="secondary" size="sm" className="mr-2" onClick={() => setEditPolicy(p)}>{t('common.edit', 'Edit')}</Button>
                    <Button variant="danger" size="sm" onClick={() => deleteClsMutation.mutate(p.id)}>{t('common.delete', 'Delete')}</Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function RlsPolicyForm({ policy, projects, roles, currentProjectId, onClose, onSaved, onError }: {
  policy: RowSecurityPolicy | null
  projects: any[]
  roles: any[]
  currentProjectId?: number
  onClose: () => void
  onSaved: () => void
  onError: (msg: string) => void
}) {
  const t = useI18nStore((s) => s.t)
  const [projectId, setProjectId] = useState(policy?.project_id ?? currentProjectId ?? 0)
  const [roleId, setRoleId] = useState(policy?.role_id ?? 0)
  const [modelName, setModelName] = useState(policy?.model_name ?? '')
  const [columnName, setColumnName] = useState(policy?.column_name ?? '')
  const [operator, setOperator] = useState(policy?.operator ?? '=')
  const [value, setValue] = useState(policy?.value ?? '')
  const [valueSource, setValueSource] = useState<'literal' | 'user_attribute'>(policy?.value_source === 'user_attribute' ? 'user_attribute' : 'literal')
  const [userAttribute, setUserAttribute] = useState(policy?.user_attribute ?? '')
  const [description, setDescription] = useState(policy?.description ?? '')
  const [isEnabled, setIsEnabled] = useState(policy?.is_enabled ?? true)
  const queryClient = useQueryClient()

  const effectiveProjectId = projectId || currentProjectId

  const { data: models = [] } = useQuery({
    queryKey: ['models-for-rls', effectiveProjectId],
    queryFn: () => effectiveProjectId ? modelingApi.models.list(effectiveProjectId) : Promise.resolve([]),
    enabled: !!effectiveProjectId,
  })

  const saveMutation = useMutation({
    mutationFn: () => {
      const data = { project_id: projectId, role_id: roleId, model_name: modelName, column_name: columnName, operator, value: value || null, value_source: valueSource, user_attribute: userAttribute || null, description: description || null, is_enabled: isEnabled }
      if (policy) return adminSecurityPoliciesApi.rls.update(policy.id, data)
      return adminSecurityPoliciesApi.rls.create(data)
    },
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['rls-policies'] }); onSaved() },
    onError: () => onError(t('admin.securityPolicies.failedToSave', 'Failed to save policy')),
  })

  return (
    <div className="mb-6 rounded-lg border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-800">
      <h3 className="mb-3 font-medium">{policy ? t('admin.securityPolicies.editRls', 'Edit Row-Level Policy') : t('admin.securityPolicies.createRls', 'Create Row-Level Policy')}</h3>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400">{t('admin.securityPolicies.project', 'Project')}</label>
          <select value={projectId} onChange={(e) => setProjectId(Number(e.target.value) || 0)} className="w-full rounded border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-700">
            {projects.map((p: any) => <option key={p.id} value={p.id}>{p.display_name || p.name}</option>)}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400">{t('admin.securityPolicies.role', 'Role')}</label>
          <select value={roleId} onChange={(e) => setRoleId(Number(e.target.value) || 0)} className="w-full rounded border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-700">
            <option value={0}>{t('admin.securityPolicies.selectRole', 'Select role...')}</option>
            {roles.map((r: any) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400">{t('admin.securityPolicies.model', 'Model')}</label>
          <select value={modelName} onChange={(e) => setModelName(e.target.value)} className="w-full rounded border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-700">
            <option value="">{t('admin.securityPolicies.selectModel', 'Select model...')}</option>
            {models.map((m: any) => <option key={m.id} value={m.name}>{m.display_name || m.name}</option>)}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400">{t('admin.securityPolicies.column', 'Column')}</label>
          <input type="text" value={columnName} onChange={(e) => setColumnName(e.target.value)} className="w-full rounded border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-700" />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400">{t('admin.securityPolicies.operator', 'Operator')}</label>
          <select value={operator} onChange={(e) => setOperator(e.target.value)} className="w-full rounded border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-700">
            {['=', '!=', '<', '>', '<=', '>=', 'IN', 'NOT IN', 'LIKE', 'NOT LIKE', 'IS NULL', 'IS NOT NULL'].map(op => <option key={op} value={op}>{op}</option>)}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400">{t('admin.securityPolicies.valueSource', 'Value Source')}</label>
          <select value={valueSource} onChange={(e) => setValueSource(e.target.value as 'literal' | 'user_attribute')} className="w-full rounded border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-700">
            <option value="literal">{t('admin.securityPolicies.literal', 'Literal')}</option>
            <option value="user_attribute">{t('admin.securityPolicies.userAttribute', 'User Attribute')}</option>
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400">{valueSource === 'literal' ? t('admin.securityPolicies.value', 'Value') : t('admin.securityPolicies.userAttribute', 'User Attribute')}</label>
          <input type="text" value={valueSource === 'literal' ? value : userAttribute} onChange={(e) => valueSource === 'literal' ? setValue(e.target.value) : setUserAttribute(e.target.value)} className="w-full rounded border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-700" />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400">{t('admin.securityPolicies.description', 'Description')}</label>
          <input type="text" value={description} onChange={(e) => setDescription(e.target.value)} className="w-full rounded border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-700" />
        </div>
        <div className="flex items-end">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={isEnabled} onChange={(e) => setIsEnabled(e.target.checked)} className="rounded" />
            {t('admin.securityPolicies.enabled', 'Enabled')}
          </label>
        </div>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="secondary" size="sm" onClick={onClose}>{t('common.cancel', 'Cancel')}</Button>
        <Button size="sm" disabled={!modelName || !columnName || !roleId} onClick={() => saveMutation.mutate()}>
          {policy ? t('common.save', 'Save') : t('common.create', 'Create')}
        </Button>
      </div>
    </div>
  )
}

function ClsPolicyForm({ policy, projects, roles, currentProjectId, onClose, onSaved, onError }: {
  policy: ColumnSecurityPolicy | null
  projects: any[]
  roles: any[]
  currentProjectId?: number
  onClose: () => void
  onSaved: () => void
  onError: (msg: string) => void
}) {
  const t = useI18nStore((s) => s.t)
  const [projectId, setProjectId] = useState(policy?.project_id ?? currentProjectId ?? 0)
  const [roleId, setRoleId] = useState(policy?.role_id ?? 0)
  const [modelName, setModelName] = useState(policy?.model_name ?? '')
  const [columnName, setColumnName] = useState(policy?.column_name ?? '')
  const [accessType, setAccessType] = useState<'HIDE' | 'MASK'>(policy?.access_type === 'MASK' ? 'MASK' : 'HIDE')
  const [maskWith, setMaskWith] = useState(policy?.mask_with ?? '***')
  const [isEnabled, setIsEnabled] = useState(policy?.is_enabled ?? true)
  const queryClient = useQueryClient()

  const effectiveProjectId = projectId || currentProjectId

  const { data: models = [] } = useQuery({
    queryKey: ['models-for-cls', effectiveProjectId],
    queryFn: () => effectiveProjectId ? modelingApi.models.list(effectiveProjectId) : Promise.resolve([]),
    enabled: !!effectiveProjectId,
  })

  const saveMutation = useMutation({
    mutationFn: () => {
      const data = { project_id: projectId, role_id: roleId, model_name: modelName, column_name: columnName, access_type: accessType, mask_with: accessType === 'MASK' ? maskWith : null, is_enabled: isEnabled }
      if (policy) return adminSecurityPoliciesApi.cls.update(policy.id, data)
      return adminSecurityPoliciesApi.cls.create(data)
    },
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['cls-policies'] }); onSaved() },
    onError: () => onError(t('admin.securityPolicies.failedToSave', 'Failed to save policy')),
  })

  return (
    <div className="mb-6 rounded-lg border border-gray-200 bg-gray-50 p-4 dark:border-gray-700 dark:bg-gray-800">
      <h3 className="mb-3 font-medium">{policy ? t('admin.securityPolicies.editCls', 'Edit Column-Level Policy') : t('admin.securityPolicies.createCls', 'Create Column-Level Policy')}</h3>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400">{t('admin.securityPolicies.project', 'Project')}</label>
          <select value={projectId} onChange={(e) => setProjectId(Number(e.target.value) || 0)} className="w-full rounded border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-700">
            {projects.map((p: any) => <option key={p.id} value={p.id}>{p.display_name || p.name}</option>)}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400">{t('admin.securityPolicies.role', 'Role')}</label>
          <select value={roleId} onChange={(e) => setRoleId(Number(e.target.value) || 0)} className="w-full rounded border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-700">
            <option value={0}>{t('admin.securityPolicies.selectRole', 'Select role...')}</option>
            {roles.map((r: any) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400">{t('admin.securityPolicies.model', 'Model')}</label>
          <select value={modelName} onChange={(e) => setModelName(e.target.value)} className="w-full rounded border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-700">
            <option value="">{t('admin.securityPolicies.selectModel', 'Select model...')}</option>
            {models.map((m: any) => <option key={m.id} value={m.name}>{m.display_name || m.name}</option>)}
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400">{t('admin.securityPolicies.column', 'Column')}</label>
          <input type="text" value={columnName} onChange={(e) => setColumnName(e.target.value)} className="w-full rounded border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-700" />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400">{t('admin.securityPolicies.accessType', 'Access Type')}</label>
          <select value={accessType} onChange={(e) => setAccessType(e.target.value as 'HIDE' | 'MASK')} className="w-full rounded border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-700">
            <option value="HIDE">HIDE</option>
            <option value="MASK">MASK</option>
          </select>
        </div>
        {accessType === 'MASK' && (
          <div>
            <label className="mb-1 block text-xs font-medium text-gray-600 dark:text-gray-400">{t('admin.securityPolicies.maskWith', 'Mask With')}</label>
            <input type="text" value={maskWith} onChange={(e) => setMaskWith(e.target.value)} className="w-full rounded border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-700" />
          </div>
        )}
        <div className="flex items-end">
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={isEnabled} onChange={(e) => setIsEnabled(e.target.checked)} className="rounded" />
            {t('admin.securityPolicies.enabled', 'Enabled')}
          </label>
        </div>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="secondary" size="sm" onClick={onClose}>{t('common.cancel', 'Cancel')}</Button>
        <Button size="sm" disabled={!modelName || !columnName || !roleId} onClick={() => saveMutation.mutate()}>
          {policy ? t('common.save', 'Save') : t('common.create', 'Create')}
        </Button>
      </div>
    </div>
  )
}