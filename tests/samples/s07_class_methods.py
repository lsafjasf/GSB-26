class Service:
    """服务类。"""

    # 方法文档
    def start(self, port: int = 8080) -> bool:
        """启动服务。

        Args:
            port (int): 监听端口

        Returns:
            bool: 是否成功
        """
        return True

    @staticmethod
    def chain():
        # 方法链调用不应干扰解析
        return Service().start().__class__

    async def fetch(self, url):
        """异步抓取。"""
        return url

    def outer(self):
        # 嵌套函数前的注释属于嵌套函数
        def helper():
            return 1
        return helper()
