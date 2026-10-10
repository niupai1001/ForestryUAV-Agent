# 账目汇总说明

读取 transactions.csv 的全部交易。每条记录为一笔交易，amount 单位为元，currency 为 CNY。
输出真实交易笔数、总金额和平均金额，平均金额不能作整数截断。使用实际安装的 six==1.17.0，
版本字符串须从 importlib.metadata 读取。结果 JSON 字段为 rows、total、mean、currency、six_version。
这是一组人工生成的精确测试数据，只用于验证软件行为，不代表林业测量或业务总体。
