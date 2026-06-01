// ================================
// 文件管理 & 用户组管理
// ================================

// ==================== 文件管理功能 ====================

function showProgress(title) {
    var overlay = document.getElementById('progressOverlay');
    var bar = document.getElementById('progressBar');
    var text = document.getElementById('progressText');
    var titleEl = document.getElementById('progressTitle');
    if (!overlay) return;
    titleEl.textContent = title || '处理中...';
    bar.style.width = '0%';
    text.textContent = '0%';
    overlay.classList.add('active');
}

function updateProgress(percent) {
    var bar = document.getElementById('progressBar');
    var text = document.getElementById('progressText');
    if (!bar || !text) return;
    var p = Math.round(percent);
    bar.style.width = p + '%';
    text.textContent = p + '%';
}

function hideProgress() {
    var overlay = document.getElementById('progressOverlay');
    if (!overlay) return;
    overlay.classList.remove('active');
}



function showUploadFileModal() {
    document.getElementById('uploadFileInput').value = '';
    document.getElementById('uploadFileDesc').value = '';
    document.getElementById('uploadFileMaxDownloads').value = '5';
    new bootstrap.Modal(document.getElementById('uploadFileModal')).show();
}

function submitUploadFile() {
    var fileInput = document.getElementById('uploadFileInput');
    var file = fileInput.files[0];
    if (!file) { showToast('请选择文件', 'warning'); return; }
    var formData = new FormData();
    formData.append('file', file);
    formData.append('description', document.getElementById('uploadFileDesc').value);
    formData.append('max_downloads', document.getElementById('uploadFileMaxDownloads').value);

    showProgress('上传文件中...');

    var xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/files', true);
    xhr.setRequestHeader('Authorization', 'Bearer ' + getAuthToken());

    xhr.upload.onprogress = function(e) {
        if (e.lengthComputable) {
            updateProgress((e.loaded / e.total) * 100);
        }
    };

    xhr.onload = function() {
        hideProgress();
        try {
            var result = JSON.parse(xhr.responseText);
            if (result.success) {
                showToast('文件上传成功', 'success');
                bootstrap.Modal.getInstance(document.getElementById('uploadFileModal')).hide();
                loadAdminFiles();
            } else {
                showToast('上传失败: ' + (result.message || '未知错误'), 'danger');
            }
        } catch(e) {
            showToast('上传失败', 'danger');
        }
    };

    xhr.onerror = function() {
        hideProgress();
        showToast('上传失败: 网络错误', 'danger');
    };

    xhr.send(formData);
}

function showEditFileModal(fileId, description, maxDownloads) {
    document.getElementById('editFileId').value = fileId;
    document.getElementById('editFileDesc').value = description || '';
    document.getElementById('editFileMaxDownloads').value = maxDownloads;
    new bootstrap.Modal(document.getElementById('editFileModal')).show();
}

async function submitEditFile() {
    var fileId = document.getElementById('editFileId').value;
    var formData = new FormData();
    formData.append('description', document.getElementById('editFileDesc').value);
    formData.append('max_downloads', document.getElementById('editFileMaxDownloads').value);
    try {
        var response = await fetch('/api/files/' + fileId, {
            method: 'PUT',
            headers: { 'Authorization': 'Bearer ' + getAuthToken() },
            body: formData
        });
        var result = await response.json();
        if (result.success) {
            showToast('文件信息已更新', 'success');
            bootstrap.Modal.getInstance(document.getElementById('editFileModal')).hide();
            loadAdminFiles();
        } else {
            showToast('更新失败: ' + (result.message || '未知错误'), 'danger');
        }
    } catch(e) {
        showToast('更新失败: 网络错误', 'danger');
    }
}

async function deleteAdminFile(fileId) {
    if (!confirm('确定要删除此文件吗？此操作不可恢复。')) return;
    try {
        var response = await fetch('/api/files/' + fileId, {
            method: 'DELETE',
            headers: { 'Authorization': 'Bearer ' + getAuthToken() }
        });
        var result = await response.json();
        if (result.success) {
            showToast('文件已删除', 'success');
            loadAdminFiles();
        } else {
            showToast('删除失败: ' + (result.message || '未知错误'), 'danger');
        }
    } catch(e) {
        showToast('删除失败: 网络错误', 'danger');
    }
}

function formatSize(bytes) {
    if (!bytes) return '0 B';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + ' MB';
    return (bytes / 1073741824).toFixed(2) + ' GB';
}

async function loadAdminFiles() {
    var loading = document.getElementById('adminFileLoading');
    var list = document.getElementById('adminFileList');
    var empty = document.getElementById('adminFileEmpty');
    if (!loading || !list || !empty) return;
    
    loading.style.display = 'block';
    list.innerHTML = '';
    empty.style.display = 'none';
    
    try {
        var response = await fetch('/api/files', {
            headers: { 'Authorization': 'Bearer ' + getAuthToken() }
        });
        var result = await response.json();
        loading.style.display = 'none';
        
        if (!result.success || !result.data || !result.data.length) {
            empty.style.display = 'block';
            return;
        }
        
        var files = result.data;
        var html = '<div class="table-responsive"><table class="table table-hover">';
        html += '<thead><tr><th>ID</th><th>文件名</th><th>描述</th><th>大小</th><th>上限/次</th><th>操作</th></tr></thead><tbody>';
        
        for (var i = 0; i < files.length; i++) {
            var f = files[i];
            html += '<tr>';
            html += '<td>' + f.id + '</td>';
            html += '<td>' + (f.filename || '') + '</td>';
            html += '<td>' + (f.description || '-') + '</td>';
            html += '<td>' + formatSize(f.file_size) + '</td>';
            html += '<td>' + f.max_downloads_per_user + '</td>';
            html += '<td>';
            html += '<button class="btn btn-sm btn-outline-primary me-1" data-action="edit" data-id="' + f.id + '" data-desc="' + (f.description || '').replace(/"/g, '&quot;') + '" data-max="' + f.max_downloads_per_user + '"><i class="bi bi-pencil"></i></button>';
            html += '<button class="btn btn-sm btn-outline-danger" data-action="delete" data-id="' + f.id + '"><i class="bi bi-trash"></i></button>';
            html += '</td>';
            html += '</tr>';
        }
        
        html += '</tbody></table></div>';
        list.innerHTML = html;
        
        // Use event delegation instead of inline onclick
        list.querySelectorAll('button[data-action="edit"]').forEach(function(btn) {
            btn.addEventListener('click', function() {
                showEditFileModal(
                    parseInt(this.getAttribute('data-id')),
                    this.getAttribute('data-desc'),
                    parseInt(this.getAttribute('data-max'))
                );
            });
        });
        
        list.querySelectorAll('button[data-action="delete"]').forEach(function(btn) {
            btn.addEventListener('click', function() {
                deleteAdminFile(parseInt(this.getAttribute('data-id')));
            });
        });
        
    } catch(e) {
        loading.style.display = 'none';
        showToast('加载文件列表失败', 'danger');
    }
}



// API helpers for user group management
async function apiGet(url) {
    const res = await fetchJSON(url);
    return res;
}

async function apiPost(url, data) {
    const res = await fetchJSON(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
    return res;
}

async function apiDelete(url) {
    const res = await fetchJSON(url, { method: 'DELETE', headers: { 'Content-Type': 'application/json' } });
    return res;
}
// ==================== 用户组管理 ====================

let _currentGroupId = null;

function showCreateGroupModal() {
    new bootstrap.Modal(document.getElementById('createGroupModal')).show();
}

async function submitCreateGroup() {
    const name = document.getElementById('groupNameInput').value.trim();
    const desc = document.getElementById('groupDescInput').value.trim();
    const count = parseInt(document.getElementById('groupUserCount').value) || 5;
    if (!name) { showToast('请输入组名', 'warning'); return; }
    try {
        const res = await apiPost('/api/groups', { group_name: name, description: desc, user_count: count });
        if (res.success) {
            bootstrap.Modal.getInstance(document.getElementById('createGroupModal')).hide();
            showToast(res.message, 'success');
            document.getElementById('createGroupForm').reset();
            loadGroups();
        } else {
            showToast(res.message || '创建失败', 'danger');
        }
    } catch(e) {
        showToast('创建用户组失败: ' + e.message, 'danger');
    }
}

async function loadGroups() {
    const loading = document.getElementById('groupListLoading');
    const list = document.getElementById('groupList');
    const empty = document.getElementById('groupListEmpty');
    loading.style.display = 'block';
    list.innerHTML = '';
    empty.style.display = 'none';
    try {
        const res = await apiGet('/api/groups');
        if (!res.success || !res.data || !res.data.length) {
            empty.style.display = 'block';
            return;
        }
        var html = '<div class="table-responsive"><table class="table table-hover"><thead><tr><th>组名</th><th>描述</th><th>成员数</th><th>创建时间</th><th>操作</th></tr></thead><tbody>';
        for (var i = 0; i < res.data.length; i++) {
            var g = res.data[i];
            html += '<tr><td><strong>' + escapeHtml(g.group_name) + '</strong></td>' +
                '<td>' + escapeHtml(g.description || '-') + '</td>' +
                '<td><span class="badge bg-info">' + (g.member_count || 0) + '</span></td>' +
                '<td>' + escapeHtml(g.created_at || '-') + '</td>' +
                '<td><button class="btn btn-sm btn-outline-info me-1" onclick="viewGroupMembers(' + g.id + ')"><i class="bi bi-eye me-1"></i>成员</button>' +
                '<button class="btn btn-sm btn-outline-danger" onclick="deleteGroup(' + g.id + ', \'' + escapeHtml(g.group_name).replace(/'/g, "\\'") + '\')"><i class="bi bi-trash me-1"></i>删除</button></td></tr>';
        }
        html += '</tbody></table></div>';
        list.innerHTML = html;
    } catch(e) {
        loading.style.display = 'none';
        showToast('加载用户组失败', 'danger');
    } finally {
        loading.style.display = 'none';
    }
}

async function viewGroupMembers(groupId) {
    _currentGroupId = groupId;
    var modal = new bootstrap.Modal(document.getElementById('viewMembersModal'));
    var tbody = document.getElementById('membersTableBody');
    var empty = document.getElementById('membersEmpty');
    tbody.innerHTML = '<tr><td colspan="4" class="text-center"><div class="spinner-border spinner-border-sm me-2"></div>加载中...</td></tr>';
    empty.style.display = 'none';
    modal.show();
    try {
        var res = await apiGet('/api/groups/' + groupId + '/members');
        if (!res.success || !res.data || !res.data.length) {
            tbody.innerHTML = '';
            empty.style.display = 'block';
            return;
        }
        var html = '';
        for (var i = 0; i < res.data.length; i++) {
            var m = res.data[i];
            html += '<tr><td>' + escapeHtml(m.username) + '</td>' +
                '<td><code>' + escapeHtml(m.password_plain || '-') + '</code></td>' +
                '<td>' + escapeHtml(m.created_at || '-') + '</td>' +
                '<td><button class="btn btn-sm btn-outline-danger" onclick="removeGroupMember(' + groupId + ', ' + m.id + ')"><i class="bi bi-person-x me-1"></i>移除</button></td></tr>';
        }
        tbody.innerHTML = html;
    } catch(e) {
        tbody.innerHTML = '<tr><td colspan="4" class="text-center text-danger">加载失败: ' + escapeHtml(e.message) + '</td></tr>';
    }
}

async function deleteGroup(groupId, groupName) {
    if (!confirm('确定要删除用户组 "' + groupName + '" 及其所有成员吗？此操作不可撤销。')) return;
    try {
        var res = await apiDelete('/api/groups/' + groupId);
        if (res.success) {
            showToast('用户组已删除', 'success');
            loadGroups();
        } else {
            showToast(res.message || '删除失败', 'danger');
        }
    } catch(e) {
        showToast('删除用户组失败: ' + e.message, 'danger');
    }
}

function addMembersToCurrentGroup() {
    if (!_currentGroupId) { showToast('请先选择一个用户组', 'warning'); return; }
    new bootstrap.Modal(document.getElementById('addMembersModal')).show();
}

async function submitAddMembers() {
    var count = parseInt(document.getElementById('addMemberCount').value) || 5;
    if (!_currentGroupId) { showToast('用户组信息丢失', 'danger'); return; }
    try {
        var res = await apiPost('/api/groups/' + _currentGroupId + '/members', { count: count });
        if (res.success) {
            bootstrap.Modal.getInstance(document.getElementById('addMembersModal')).hide();
            showToast(res.message, 'success');
            viewGroupMembers(_currentGroupId);
        } else {
            showToast(res.message || '添加失败', 'danger');
        }
    } catch(e) {
        showToast('添加用户失败: ' + e.message, 'danger');
    }
}

async function removeGroupMember(groupId, userId) {
    if (!confirm('确定要移除该成员吗？')) return;
    try {
        var res = await apiDelete('/api/groups/' + groupId + '/members/' + userId);
        if (res.success) {
            showToast('成员已移除', 'success');
            viewGroupMembers(groupId);
        } else {
            showToast(res.message || '移除失败', 'danger');
        }
    } catch(e) {
        showToast('移除成员失败: ' + e.message, 'danger');
    }
}
