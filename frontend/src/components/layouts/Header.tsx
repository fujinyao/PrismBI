'use client'

import { useState, useEffect, useCallback, useRef } from 'react'
import { usePathname, useRouter } from 'next/navigation'
import { useQuery } from '@tanstack/react-query'
import { useAuthStore } from '@/stores/authStore'
import { useThemeStore } from '@/stores/themeStore'
import { useI18nStore } from '@/stores/i18nStore'
import { useProjectStore } from '@/stores/projectStore'
import { useBrandingStore } from '@/stores/brandingStore'
import { displayThreadSummary } from '@/lib/utils'
import { dashboardApi, projectsApi, threadsApi, type ThreadDetail } from '@/lib/api'

const breadcrumbKeys: Record<string, string> = {
  '/home': 'nav.home',
  '/home/dashboard': 'nav.dashboard',
  '/modeling': 'nav.modeling',
  '/knowledge/instructions': 'nav.knowledge',
  '/knowledge/question-sql-pairs': 'nav.knowledge.sqlPairs',
  '/api-management/history': 'nav.apiManagement.history',
  '/settings': 'nav.settings',
  '/settings/datasources': 'nav.settings.datasources',
  '/settings/recommendations': 'nav.settings.recommendations',
  '/settings/recommendations/scores': 'recommendation.history',
  '/settings/profile': 'nav.settings.profile',
  '/settings/profile/sessions': 'nav.settings.sessions',
  '/admin/users': 'nav.admin.users',
  '/admin/roles': 'nav.admin.roles',
  '/admin/audit': 'nav.admin.audit',
  '/admin/backup': 'nav.admin.backup',
  '/projects': 'nav.projectSettings',
}

export function Header() {
  const pathname = usePathname()
  const router = useRouter()
  const user = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const hasPermission = useAuthStore((s) => s.hasPermission)
  const { mode, setMode, getEffectiveMode } = useThemeStore()
  const t = useI18nStore((s) => s.t)
  const appName = useBrandingStore((s) => s.appName)
  const threadIdMatch = pathname.match(/^\/home\/(\d+)/)
  const threadId = threadIdMatch ? Number(threadIdMatch[1]) : null
  const dashboardIdMatch = pathname.match(/^\/home\/dashboard\/(\d+)/)
  const dashboardId = dashboardIdMatch ? Number(dashboardIdMatch[1]) : null
  const projectSettingsIdMatch = pathname.match(/^\/projects\/(\d+)\/settings/)
  const projectSettingsId = projectSettingsIdMatch ? Number(projectSettingsIdMatch[1]) : null
  const {
    projects,
    currentProject,
    loading,
    fetchProjects,
    switchProject,
    deleteProject,
    setCurrentProject,
    loaded: projectsLoaded,
  } = useProjectStore()

  const [menuOpen, setMenuOpen] = useState(false)
  const [projectMenuOpen, setProjectMenuOpen] = useState(false)
  const deleteConfirmRef = useRef<HTMLInputElement>(null)
  const deleteConfirmComposing = useRef(false)

  const [showDatasources, setShowDatasources] = useState(false)
  const [boundDatasources, setBoundDatasources] = useState<any[]>([])
  const [datasourcesLoading, setDatasourcesLoading] = useState(false)

  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [deleteConfirmText, setDeleteConfirmText] = useState('')
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    if (!projectsLoaded && !loading) fetchProjects()
  }, [fetchProjects, loading, projectsLoaded])

  const { data: headerThread } = useQuery({
    queryKey: ['thread', threadId],
    queryFn: () => threadsApi.get(threadId!) as Promise<ThreadDetail>,
    enabled: Boolean(threadId),
  })

  const { data: headerDashboard } = useQuery({
    queryKey: ['dashboard', dashboardId],
    queryFn: () => dashboardApi.get(dashboardId!) as Promise<{ name: string; display_name?: string }>,
    enabled: Boolean(dashboardId),
  })

  const { data: headerProject } = useQuery({
    queryKey: ['project', projectSettingsId],
    queryFn: () => projectsApi.get(projectSettingsId!) as Promise<{ name: string; display_name?: string; displayName?: string }>,
    enabled: Boolean(projectSettingsId),
  })

  const titleKey = Object.entries(breadcrumbKeys)
    .sort(([a], [b]) => b.length - a.length)
    .find(([path]) => pathname.startsWith(path))?.[1]
  const baseTitle = titleKey ? t(titleKey) : appName
  const title = threadId
    ? `${t('nav.home', 'Home')} >> ${displayThreadSummary(headerThread?.summary, t)}`
    : dashboardId
      ? `${t('nav.dashboard', 'Dashboards')} >> ${headerDashboard?.display_name || headerDashboard?.name || t('dashboard.title', 'Dashboards')}`
      : projectSettingsId
        ? `${t('nav.projectSettings', 'Project Settings')} >> ${headerProject?.display_name || headerProject?.displayName || headerProject?.name || t('project.settings', 'Project Settings')}`
        : pathname.startsWith('/knowledge/instructions')
          ? `${t('nav.knowledge', 'Knowledge')} >> ${t('nav.knowledge.instructions', 'Instructions')}`
          : pathname.startsWith('/knowledge/question-sql-pairs')
            ? `${t('nav.knowledge', 'Knowledge')} >> ${t('nav.knowledge.sqlPairs', 'Question-SQL Pairs')}`
            : pathname.startsWith('/settings/profile/sessions')
              ? `${t('nav.settings', 'Settings')} >> ${t('nav.settings.sessions', 'Sessions')}`
              : pathname.startsWith('/settings/profile')
                ? `${t('nav.settings', 'Settings')} >> ${t('nav.settings.profile', 'Profile')}`
                : pathname.startsWith('/settings/recommendations/scores')
                  ? `${t('nav.settings', 'Settings')} >> ${t('recommendation.history', 'Score History')}`
                  : pathname.startsWith('/settings/recommendations')
                    ? `${t('nav.settings', 'Settings')} >> ${t('nav.settings.recommendations', 'Recommendations')}`
                    : pathname.startsWith('/settings/datasources')
                      ? `${t('nav.settings', 'Settings')} >> ${t('nav.settings.datasources', 'Data Sources')}`
                      : pathname.startsWith('/admin/users')
                        ? `${t('nav.admin', 'Admin')} >> ${t('nav.admin.users', 'Users')}`
                        : pathname.startsWith('/admin/roles')
                          ? `${t('nav.admin', 'Admin')} >> ${t('nav.admin.roles', 'Roles')}`
                          : pathname.startsWith('/admin/audit')
                            ? `${t('nav.admin', 'Admin')} >> ${t('nav.admin.audit', 'Audit Log')}`
                            : baseTitle

  const otherProjects = projects.filter((p) => !p.is_current)
  const canCreateProject = hasPermission('admin', 'manage')
  const canDeleteProject = hasPermission('admin', 'manage')
  const canManageProject = hasPermission('projects', 'update')
  const hasProjectActions = canCreateProject || (Boolean(currentProject) && (canDeleteProject || canManageProject))
  const canOpenEmptyProject = Boolean(currentProject)

  const handleSwitchProject = async (id: number) => {
    await switchProject(id)
    setProjectMenuOpen(false)
    router.push('/modeling')
  }

  const handleCreateProject = () => {
    router.push('/setup/connection')
    setProjectMenuOpen(false)
  }

  const handleOpenEmptyProject = () => {
    setCurrentProject(null)
    setProjectMenuOpen(false)
    router.push('/home')
  }

  const openDatasourceModal = useCallback(async () => {
    if (!currentProject) return
    setDatasourcesLoading(true)
    setShowDatasources(true)
    setProjectMenuOpen(false)
    try {
      const datasources = await projectsApi.datasources.list(currentProject.id) as any[]
      setBoundDatasources(datasources ?? [])
    } catch {
      setBoundDatasources([])
    } finally {
      setDatasourcesLoading(false)
    }
  }, [currentProject])

  const handleUnbind = async (bindingId: number) => {
    if (!currentProject) return
    try {
      await projectsApi.datasources.unbind(currentProject.id, bindingId)
      const datasources = await projectsApi.datasources.list(currentProject.id) as any[]
      setBoundDatasources(datasources ?? [])
    } catch {
      /* error handled by api interceptor */
    }
  }

  const handleDeleteProject = async () => {
    const expectedConfirm = t('header.confirmDeleteKeyword', 'delete')
    if (!currentProject || deleteConfirmText !== expectedConfirm) return
    const deletedProjectId = currentProject.id
    setDeleting(true)
    try {
      await deleteProject(deletedProjectId)
      setShowDeleteConfirm(false)
      setDeleteConfirmText('')
      setProjectMenuOpen(false)
      const { projects } = useProjectStore.getState()
      const remaining = projects.filter((project) => project.id !== deletedProjectId)
      if (remaining.length === 0) {
        router.push('/home')
      } else {
        const nextProject = remaining[0]
        if (nextProject) {
          await useProjectStore.getState().switchProject(nextProject.id)
        }
        const switchedProject = useProjectStore.getState().currentProject
        if (!switchedProject || switchedProject.id !== nextProject?.id) {
          router.push('/home')
          return
        }
        router.push('/modeling')
      }
    } catch {
      /* error handled by api interceptor */
    } finally {
      setDeleting(false)
    }
  }

  const deleteKeyword = t('header.confirmDeleteKeyword', 'delete')
  const confirmLabel = t('header.confirmDeleteLabel', 'Type "delete" to confirm')
  const isSampleProject = currentProject?.type === 'sample'

  return (
    <header className="flex h-14 items-center justify-between border-b border-gray-200 bg-white px-6 dark:border-gray-700 dark:bg-gray-900">
      <div className="flex items-center gap-3">
        <h1 className="text-base font-semibold text-gray-900 dark:text-gray-100">{title}</h1>
      </div>

      <div className="flex items-center gap-3">
        <button
          onClick={() => setMode(getEffectiveMode() === 'dark' ? 'light' : 'dark')}
          className="rounded p-2 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
          aria-label={t('common.toggleTheme', 'Toggle theme')}
        >
          {getEffectiveMode() === 'dark' ? (
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
            </svg>
          ) : (
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
            </svg>
          )}
        </button>

        <div className="relative">
          <button
            onClick={() => { setProjectMenuOpen(!projectMenuOpen); setMenuOpen(false) }}
            className="flex items-center gap-1.5 rounded-md border border-gray-200 px-3 py-1.5 text-sm text-gray-700 hover:bg-gray-100 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800"
          >
            <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
            </svg>
            <span className="max-w-[120px] truncate">
              {currentProject
                ? (currentProject.display_name || currentProject.name)
                : t('header.emptyProject', 'Empty Project')}
            </span>
            <svg className="h-3 w-3 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          {projectMenuOpen && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setProjectMenuOpen(false)} />
              <div className="absolute right-0 z-20 mt-1 w-56 rounded-md border border-gray-200 bg-white py-1 shadow-lg dark:border-gray-700 dark:bg-gray-800">
                {otherProjects.length > 0 && (
                  <>
                    <div className="px-3 py-1.5 text-xs font-medium uppercase text-gray-400">
                      {t('header.switchProject', 'Switch Project')}
                    </div>
                    {otherProjects.map((p) => (
                      <button
                        key={p.id}
                        onClick={() => handleSwitchProject(p.id)}
                        className="flex w-full items-center gap-2 px-4 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
                      >
                        <svg className="h-3.5 w-3.5 shrink-0 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
                        </svg>
                        <span className="truncate">{p.display_name || p.name}</span>
                      </button>
                    ))}
                  </>
                )}

                {canOpenEmptyProject && (
                  <>
                    {otherProjects.length > 0 && <hr className="my-1 border-gray-200 dark:border-gray-700" />}
                    <button
                      onClick={handleOpenEmptyProject}
                      className="flex w-full items-center gap-2 px-4 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
                    >
                      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 6v6l4 2m5-2a9 9 0 11-18 0 9 9 0 0118 0z" />
                      </svg>
                      {t('header.openEmptyProject', 'Open Empty Project')}
                    </button>
                  </>
                )}

                {hasProjectActions && (
                  <>
                    {(otherProjects.length > 0 || canOpenEmptyProject) && <hr className="my-1 border-gray-200 dark:border-gray-700" />}

                    {canManageProject && currentProject && (
                      <button
                        onClick={() => { router.push(`/projects/${currentProject.id}/settings`); setProjectMenuOpen(false) }}
                        className="flex w-full items-center gap-2 px-4 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
                      >
                        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.066 2.573c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.573 1.066c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.066-2.573c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.573-1.066z" />
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                        </svg>
                        {t('nav.projectSettings', 'Project Settings')}
                      </button>
                    )}

                    {canCreateProject && (
                      <button
                        onClick={handleCreateProject}
                        className="flex w-full items-center gap-2 px-4 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
                      >
                        <svg className="h-4 w-4 text-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                        </svg>
                        {t('header.newProject', 'New Project')}
                      </button>
                    )}

                    {canDeleteProject && currentProject && (
                      <button
                        onClick={() => { setShowDeleteConfirm(true); setProjectMenuOpen(false) }}
                        className="flex w-full items-center gap-2 px-4 py-2 text-left text-sm text-error hover:bg-gray-100 dark:hover:bg-gray-700"
                      >
                        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                        </svg>
                        {t('project.delete', 'Delete Project')}
                      </button>
                    )}
                  </>
                )}
              </div>
            </>
          )}
        </div>

        <div className="relative">
          <button
            onClick={() => { setMenuOpen(!menuOpen); setProjectMenuOpen(false) }}
            className="flex items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-gray-100 dark:hover:bg-gray-800"
          >
            <div className="flex h-7 w-7 items-center justify-center rounded-full bg-primary text-xs font-medium text-white">
              {user?.display_name?.[0]?.toUpperCase() || user?.username[0]?.toUpperCase() || 'U'}
            </div>
            <span className="hidden text-gray-700 dark:text-gray-300 sm:inline">
              {user?.display_name || user?.username}
            </span>
          </button>

          {menuOpen && (
            <>
              <div className="fixed inset-0 z-10" onClick={() => setMenuOpen(false)} />
              <div className="absolute right-0 z-20 mt-1 w-48 rounded-md border border-gray-200 bg-white py-1 shadow-lg dark:border-gray-700 dark:bg-gray-800">
                <button
                  onClick={() => { router.push('/settings/profile'); setMenuOpen(false) }}
                  className="block w-full px-4 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
                >
                  {t('nav.settings.profile')}
                </button>
                <button
                  onClick={() => { router.push('/settings/profile/sessions'); setMenuOpen(false) }}
                  className="block w-full px-4 py-2 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700"
                >
                  {t('nav.settings.sessions')}
                </button>
                <hr className="my-1 border-gray-200 dark:border-gray-700" />
                <button
                  onClick={logout}
                  className="block w-full px-4 py-2 text-left text-sm text-error hover:bg-gray-100 dark:hover:bg-gray-700"
                >
                  {t('nav.signOut')}
                </button>
              </div>
            </>
          )}
        </div>
      </div>

      {showDatasources && currentProject && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setShowDatasources(false)}>
          <div className="w-full max-w-lg rounded-lg bg-white p-6 shadow-xl dark:bg-gray-800" onClick={(e) => e.stopPropagation()}>
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                {t('header.datasourceManage', 'Manage Data Sources')}
              </h2>
              <button
                onClick={() => setShowDatasources(false)}
                className="rounded p-1 text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700"
              >
                <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            <p className="mb-4 text-sm text-gray-500 dark:text-gray-400">
              {currentProject.display_name || currentProject.name}
            </p>

            {datasourcesLoading ? (
              <div className="py-8 text-center text-sm text-gray-400">
                {t('common.loading', 'Loading...')}
              </div>
            ) : boundDatasources.length === 0 ? (
              <div className="rounded-md border border-dashed border-gray-300 p-6 text-center dark:border-gray-600">
                <p className="text-sm text-gray-500 dark:text-gray-400">
                  {t('header.noBoundDatasources', 'No datasources bound')}
                </p>
                <button
                  onClick={() => { setShowDatasources(false); router.push(`/projects/${currentProject.id}/settings`) }}
                  className="mt-3 text-sm text-primary hover:underline"
                >
                  {t('header.goToSettings', 'Go to Settings')}
                </button>
              </div>
            ) : (
              <div className="space-y-2">
                {boundDatasources.map((ds: any) => (
                  <div
                    key={ds.id}
                    className="flex items-center justify-between rounded-md border border-gray-200 p-3 dark:border-gray-700"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-gray-900 dark:text-gray-100">
                        {ds.alias || ds.datasource_name || ds.datasource?.name || ds.name}
                      </p>
                      <p className="text-xs text-gray-500 dark:text-gray-400">{ds.datasource_type || ds.datasource?.type || ds.type}</p>
                    </div>
                    <button
                      onClick={() => {
                        if (confirm(t('project.unbindConfirm', 'Unbind this datasource?'))) {
                          handleUnbind(ds.bindingId ?? ds.id)
                        }
                      }}
                      className="ml-3 shrink-0 text-xs text-red-500 hover:text-red-700"
                    >
                      {t('project.unbind', 'Unbind')}
                    </button>
                  </div>
                ))}
              </div>
            )}

            <div className="mt-5 flex justify-end">
              <button
                onClick={() => { setShowDatasources(false); router.push(`/projects/${currentProject.id}/settings`) }}
                className="rounded-md bg-primary px-4 py-2 text-sm text-white hover:bg-primary/90"
              >
                {t('header.goToSettings', 'Go to Settings')}
              </button>
            </div>
          </div>
        </div>
      )}

      {showDeleteConfirm && currentProject && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setShowDeleteConfirm(false)}>
          <div className="w-full max-w-sm rounded-lg bg-white p-6 shadow-xl dark:bg-gray-800" onClick={(e) => e.stopPropagation()}>
            <div className="mb-1 flex items-center gap-2 text-error">
              <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
              </svg>
              <h2 className="text-lg font-semibold">
                {t('project.delete', 'Delete Project')}
              </h2>
            </div>

            <p className="mb-1 text-sm text-gray-600 dark:text-gray-400">
              {isSampleProject
                ? t('header.deleteSampleHint', 'This is a sample project used for demonstrating features to new users. It is recommended not to delete it.')
                : t('header.deleteProjectHint', 'This action cannot be undone. All project data will be permanently deleted.')}
            </p>
            <p className="mb-3 text-sm font-medium text-gray-700 dark:text-gray-300">
              {currentProject.display_name || currentProject.name}
            </p>

            <label className="mb-1 block text-sm font-medium text-gray-700 dark:text-gray-300">
              {confirmLabel}
            </label>
            <input
              ref={deleteConfirmRef}
              onInput={(e) => { if (!deleteConfirmComposing.current && !(e.nativeEvent as InputEvent).isComposing) setDeleteConfirmText((e.target as HTMLInputElement).value) }}
              onCompositionStart={() => { deleteConfirmComposing.current = true }}
              onCompositionEnd={(e) => { deleteConfirmComposing.current = false; setDeleteConfirmText((e.target as HTMLInputElement).value) }}
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100"
              placeholder={t('header.confirmDeleteRequired', 'Please type delete to confirm')}
              autoFocus
            />

            <div className="mt-5 flex justify-end gap-2">
              <button
                onClick={() => { setShowDeleteConfirm(false); setDeleteConfirmText('') }}
                className="rounded-md px-4 py-2 text-sm text-gray-600 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700"
              >
                {t('common.cancel', 'Cancel')}
              </button>
              <button
                onClick={handleDeleteProject}
                disabled={deleteConfirmText !== deleteKeyword || deleting}
                className="rounded-md bg-error px-4 py-2 text-sm text-white hover:bg-error/90 disabled:opacity-50"
              >
                {deleting ? t('common.deleting', 'Deleting...') : t('common.delete', 'Delete')}
              </button>
            </div>
          </div>
        </div>
      )}
    </header>
  )
}
