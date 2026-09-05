"""Strangler-Fig 迁移期上下文（P1 拆分中间层）。

路由模块经 `ctx` 在**请求时**动态解析 reply_server 的模块级符号，保证：
1. 不产生循环导入（router -> state -> (lazy) reply_server）；
2. reply_server 侧的运行时替换依然生效 —— 尤其是测试 conftest 的
   `reply_server.db_manager = ...` 这类补丁，对已迁出的路由同样可见。

迁移收尾（共享 helper 全部下沉 app/api/common 之类）后，本模块应删除。
"""


class ApiContext:
    """属性访问即转发到 reply_server 模块级名字（延迟到调用时解析）。"""

    def __getattr__(self, name: str):
        import reply_server

        return getattr(reply_server, name)


ctx = ApiContext()
